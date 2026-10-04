"""The update notice and `zerotrace update`, against a release mirror on loopback.

Nothing here reaches GitHub: `ci/no_egress.py` would stop it, and the conftest switches the
check off for every other test. These tests switch it back on and point it at a server on
127.0.0.1 that answers the way GitHub's release pages do - a redirect from `/releases/latest`
to the tag, and assets that themselves redirect to a CDN.
"""
import hashlib
import http.server
import io
import os
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from zerotrace import cli, doctor, updates
from zerotrace.config import Config
from zerotrace.updates import Cache, Install, Notifier, UpdateError

from .conftest import _clear_git_caches, git, write

ROOT = Path(__file__).resolve().parent.parent
RUNNING = "0.4.0"
NEWER = "0.5.0"
NEWEST = "0.6.0"
NOW = 1_800_000_000.0
HOUR = 60 * 60
DAY = 24 * HOUR
INSTALL_SH = "install.sh"
INSTALL_PS1 = "install.ps1"
SCRIPT = b"#!/usr/bin/env bash\nexit 0\n"
SHOWN = "update available"


class Terminal(io.StringIO):
    """stderr as a person sees it."""

    def isatty(self) -> bool:
        return True


def run(*argv) -> int:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(list(argv))
    return exit_info.value.code


# --- a GitHub-shaped release mirror, on loopback -----------------------------------------------

class Mirror:
    def __init__(self, server: http.server.ThreadingHTTPServer) -> None:
        self.server = server
        self.routes: dict[str, tuple[int, dict, bytes]] = {}
        server.routes = self.routes
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    @property
    def repo(self) -> str:
        return f"{self.base}/repo"

    def publish(self, version: str, files: dict[str, bytes] | None = None,
                sums: dict[str, str] | None = None) -> None:
        """A release page, its assets (each behind a CDN redirect) and its SHA256SUMS."""
        files = dict(files or {})
        listed = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        listed.update(sums or {})
        files["SHA256SUMS"] = "".join(f"{digest}  {name}\n"
                                      for name, digest in listed.items()).encode()
        tag = f"v{version}"
        self.routes["/repo/releases/latest"] = (302, {"Location": f"/repo/releases/tag/{tag}"}, b"")
        self.routes[f"/repo/releases/tag/{tag}"] = (200, {}, b"<html>release</html>")
        for name, data in files.items():
            cdn = f"/cdn/{tag}/{name}"
            self.routes[f"/repo/releases/download/{tag}/{name}"] = (302, {"Location": cdn}, b"")
            self.routes[cdn] = (200, {}, data)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):        # silence: pytest owns stderr
        return

    def _answer(self, with_body: bool) -> None:
        status, headers, body = self.server.routes.get(self.path, (404, {}, b"not found"))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self):
        self._answer(True)

    def do_HEAD(self):
        self._answer(False)


@pytest.fixture
def mirror(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    release_mirror = Mirror(server)
    monkeypatch.setenv("ZEROTRACE_REPO_URL", release_mirror.repo)
    yield release_mirror
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    """The update check switched back on, as it is on a developer's own machine, in a home of
    its own. The paths are the ones `git_env` uses, so a test may ask for both; this one just
    does not start four git processes to say so."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("ZEROTRACE_HOME", str(home / ".zerotrace"))
    monkeypatch.setenv("ZEROTRACE_POLICY", str(home / "no-org-policy.yml"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("ZEROTRACE_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(updates, "__version__", RUNNING)
    _clear_git_caches()
    yield home
    _clear_git_caches()


class Lookups:
    """Stands in for the network: names the latest release, and counts how often it is asked."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.calls = 0

    def __call__(self, timeout: float = 0.0) -> str:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def lookups(monkeypatch, answer) -> Lookups:
    fake = Lookups(answer)
    monkeypatch.setattr(updates, "fetch_latest", fake)
    return fake


def around(command: str, code: int = 0, now: float = NOW, stream=None) -> str:
    """One command's worth of Notifier; what a person saw on stderr. The lookup beside the command
    is always waited for before this returns: a thread that outlived its test would write to
    whatever home the environment points at by then."""
    terminal = Terminal() if stream is None else stream
    notifier = Notifier(command, stream=terminal, clock=lambda: now)
    notifier.begin()
    notifier.end(code)
    notifier.settle(5)
    return terminal.getvalue()


def user_config(home, text: str) -> None:
    write(home / ".zerotrace" / "config.yml", text)


@pytest.fixture
def at_a_terminal(monkeypatch):
    """The command is run by a person: stderr is a terminal, as far as the notice can tell. (The
    stream itself stays pytest's capture, so no spinner draws into it.)"""
    monkeypatch.setattr(updates, "_is_terminal", lambda stream: True)


# --- versions and checksums ----------------------------------------------------------------------

@pytest.mark.parametrize(("candidate", "current", "newer"), [
    ("0.10.0", "0.9.0", True),
    ("0.4.1", "0.4.0", True),
    ("v1.0.0", "0.9.9", True),
    ("0.4.0", "0.4.0", False),
    ("0.3.9", "0.4.0", False),
    ("0.5.0rc1", "0.4.0", False),
    ("nonsense", "0.4.0", False),
    ("0.5.0", "dev", False),
])
def test_versions_compare_as_numbers(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


@pytest.mark.parametrize("text", ["", "0.5", "0.5.0.1", "v", "0.5.0-beta", "<script>"])
def test_only_release_numbers_parse(text):
    assert updates.parse_version(text) is None


def test_checksum_files_are_read_in_both_marker_styles():
    first, second = "a" * 64, "B" * 64
    text = f"{first}  {INSTALL_SH}\n{second} *{INSTALL_PS1}\nnot a checksum line\n{first}  \n"
    assert updates.parse_sums(text) == {INSTALL_SH: first, INSTALL_PS1: second.lower()}


# --- the network: https, or this machine ---------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/getzerotrace/zerotrace",
    "http://127.0.0.1:8080/repo",
    "http://localhost/repo",
    "http://[::1]:9/repo",
])
def test_https_and_this_machine_are_allowed(url):
    assert updates._checked_url(url) == url


@pytest.mark.parametrize("url", [
    "http://example.com/repo", "ftp://example.com/x", "file:///etc/passwd", "https:///x", "example.com",
])
def test_anything_else_is_refused(url):
    with pytest.raises(UpdateError, match="https only"):
        updates._checked_url(url)


def test_the_repository_can_be_overridden_like_the_installers_do(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_REPO_URL", "https://git.example.test/org/zerotrace/")
    assert updates.repo_url() == "https://git.example.test/org/zerotrace"


def test_certificates_are_verified_whatever_roots_they_come_from():
    context = updates._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


class _Store:
    """An SSL context whose certificate store holds `roots` certificates."""

    def __init__(self, roots: int) -> None:
        self.roots = roots
        self.loaded: list[str] = []

    def cert_store_stats(self) -> dict:
        return {"x509_ca": self.roots}

    def load_verify_locations(self, cafile: str) -> None:
        self.loaded.append(cafile)


def test_a_machine_with_no_certificates_borrows_certifis(monkeypatch):
    certifi = pytest.importorskip("certifi")
    store = _Store(roots=0)
    monkeypatch.setattr(ssl, "create_default_context", lambda: store)
    assert updates._tls_context() is store
    assert store.loaded == [certifi.where()]


def test_a_machines_own_certificates_are_trusted_as_they_are(monkeypatch):
    store = _Store(roots=120)
    monkeypatch.setattr(ssl, "create_default_context", lambda: store)
    assert updates._tls_context() is store
    assert store.loaded == []


def test_a_missing_certifi_does_not_stop_the_lookup(monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)       # `import certifi` now raises
    store = _Store(roots=0)
    monkeypatch.setattr(ssl, "create_default_context", lambda: store)
    assert updates._tls_context() is store
    assert store.loaded == []


def test_the_latest_release_is_read_from_the_redirect(enabled, mirror):
    mirror.publish("0.10.2")
    assert updates.fetch_latest() == "0.10.2"


def test_a_repository_with_no_release_is_an_error_not_a_crash(enabled, mirror):
    with pytest.raises(UpdateError, match="could not look up"):
        updates.fetch_latest()


def test_a_redirect_to_plain_http_elsewhere_is_refused(enabled, mirror):
    mirror.routes["/repo/releases/latest"] = (
        302, {"Location": "http://example.com/repo/releases/tag/v9.9.9"}, b"")
    with pytest.raises(UpdateError, match="https only"):
        updates.fetch_latest()


def test_a_host_that_does_not_answer_is_an_error(enabled, monkeypatch):
    monkeypatch.setenv("ZEROTRACE_REPO_URL", "http://127.0.0.1:9/repo")     # nothing listens
    with pytest.raises(UpdateError, match="could not look up"):
        updates.fetch_latest(timeout=1.0)


def test_a_download_larger_than_its_limit_is_refused(enabled, mirror):
    mirror.publish(NEWER, {INSTALL_SH: b"x" * 100})
    with pytest.raises(UpdateError, match="larger than 10 bytes"):
        updates.download(f"{mirror.repo}/releases/download/v{NEWER}/{INSTALL_SH}", limit=10)


def test_the_installer_is_kept_only_when_it_matches_the_checksums(enabled, mirror, tmp_path):
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT})
    path = updates.fetch_installer(f"v{NEWER}", tmp_path / "update", INSTALL_SH)
    assert path.read_bytes() == SCRIPT


def test_a_tampered_installer_is_not_written_and_never_run(enabled, mirror, tmp_path):
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT}, sums={INSTALL_SH: "0" * 64})
    with pytest.raises(UpdateError, match="does not match its SHA256SUMS"):
        updates.fetch_installer(f"v{NEWER}", tmp_path / "update", INSTALL_SH)
    assert not (tmp_path / "update" / INSTALL_SH).exists()


def test_an_installer_the_release_does_not_list_is_refused(enabled, mirror, tmp_path):
    mirror.publish(NEWER, {INSTALL_PS1: SCRIPT})
    mirror.routes[f"/repo/releases/download/v{NEWER}/{INSTALL_SH}"] = (200, {}, SCRIPT)
    with pytest.raises(UpdateError, match="does not list"):
        updates.fetch_installer(f"v{NEWER}", tmp_path / "update", INSTALL_SH)


# --- what is remembered, and when a lookup is owed -----------------------------------------------

def test_the_cache_round_trips(enabled):
    updates.save_cache(Cache(checked_at=5.0, attempted_at=6.0, latest=NEWER,
                             notified_at=7.0, notified_for=NEWER))
    assert updates.load_cache() == Cache(5.0, 6.0, NEWER, 7.0, NEWER)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"latest": "<script>"}', '{"checked_at": "x"}'])
def test_a_damaged_cache_is_just_empty(enabled, content):
    write(updates._cache_path(), content)
    assert updates.load_cache() == Cache()


def test_a_cache_that_cannot_be_written_costs_nothing(enabled, monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("ZEROTRACE_HOME", str(blocker / "home"))
    updates.save_cache(Cache(latest=NEWER))
    assert updates.load_cache() == Cache()


@pytest.mark.parametrize(("attempted", "checked", "owed"), [
    (0.0, 0.0, True),                      # never looked
    (NOW - 60, NOW - 60, False),           # looked a minute ago
    (NOW - HOUR, NOW - HOUR, False),       # an hour ago is not a day
    (NOW - DAY - 60, NOW - DAY - 60, True),
    (NOW - 10 * 60, NOW - 3 * DAY, False),  # a failed try ten minutes ago is not repeated yet
    (NOW - 2 * HOUR, NOW - 3 * DAY, True),
    (NOW + DAY, NOW + DAY, True),          # a clock that was set back
])
def test_a_lookup_is_owed_once_a_day_and_never_hammered(attempted, checked, owed):
    assert updates.due(Cache(checked_at=checked, attempted_at=attempted), NOW) is owed


@pytest.mark.parametrize(("env", "off"), [
    ({}, False),
    ({"CI": "true"}, True),
    ({"CI": "1"}, True),
    ({"CI": "0"}, False),
    ({"CI": "false"}, False),
    ({"CI": ""}, False),
    ({"ZEROTRACE_NO_UPDATE_CHECK": "1"}, True),
    ({"NO_UPDATE_NOTIFIER": "1"}, True),
])
def test_the_environment_can_turn_the_check_off(env, off):
    assert bool(updates.opt_out_reason(env)) is off


# --- the notice, around a command ----------------------------------------------------------------

def test_a_newer_release_is_mentioned_after_a_command_that_succeeded(enabled, monkeypatch):
    lookups(monkeypatch, NEWER)
    shown = around("version")
    assert SHOWN in shown
    assert f"{RUNNING} \u2192 {NEWER}" in shown
    assert "zerotrace update" in shown


def test_nothing_is_said_when_the_command_failed(enabled, monkeypatch):
    lookups(monkeypatch, NEWER)
    assert around("version", code=1) == ""


def test_the_same_release_is_not_news(enabled, monkeypatch):
    lookups(monkeypatch, RUNNING)
    assert around("version") == ""


def test_nothing_is_looked_up_or_said_off_a_terminal(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWER)
    assert around("version", stream=io.StringIO()) == ""
    assert asked.calls == 0


@pytest.mark.parametrize("variable", ["CI", "ZEROTRACE_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER"])
def test_the_environment_switches_the_lookup_and_the_notice_off(enabled, monkeypatch, variable):
    asked = lookups(monkeypatch, NEWER)
    monkeypatch.setenv(variable, "1")
    assert around("version") == ""
    assert asked.calls == 0


def test_a_config_file_switches_it_off(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWER)
    user_config(enabled, "updates:\n  check: false\n")
    assert around("version") == ""
    assert asked.calls == 0


@pytest.mark.parametrize("command", ["gateway", "eval", "doctor", "update", "pre-push",
                                     "post-commit", "install", "setup", "uninstall", "review"])
def test_commands_that_are_read_by_programs_or_have_their_own_say_stay_silent(enabled, monkeypatch, command):
    asked = lookups(monkeypatch, NEWER)
    assert around(command) == ""
    assert asked.calls == 0


def test_a_hook_mentions_a_release_once_a_day(enabled, monkeypatch):
    lookups(monkeypatch, NEWER)
    assert SHOWN in around("run")
    assert around("run", now=NOW + 60) == ""
    assert SHOWN in around("run", now=NOW + DAY + 60)


def test_a_hook_mentions_an_even_newer_release_at_once(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWEST)
    updates.save_cache(Cache(checked_at=NOW, attempted_at=NOW, latest=NEWEST,
                             notified_at=NOW, notified_for=NEWER))
    assert NEWEST in around("run", now=NOW + 60)
    assert asked.calls == 0


def test_a_typed_command_mentions_it_every_time(enabled, monkeypatch):
    lookups(monkeypatch, NEWER)
    assert SHOWN in around("version")
    assert SHOWN in around("version", now=NOW + 60)


def test_a_fresh_answer_is_not_asked_for_again(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWER)
    around("version")
    around("version", now=NOW + HOUR)
    assert asked.calls == 1


def test_a_failed_lookup_is_not_repeated_for_an_hour(enabled, monkeypatch):
    asked = lookups(monkeypatch, UpdateError("offline"))
    around("version")
    around("version", now=NOW + 10 * 60)
    assert asked.calls == 1
    around("version", now=NOW + HOUR + 60)
    assert asked.calls == 2


def test_a_slow_lookup_never_holds_the_command_up(enabled, monkeypatch):
    release = threading.Event()

    def slow(timeout: float = 0.0) -> str:
        release.wait(10)
        return NEWER

    monkeypatch.setattr(updates, "fetch_latest", slow)
    monkeypatch.setattr(updates, "SETTLE_SECONDS", 0.05)
    terminal = Terminal()
    notifier = Notifier("version", stream=terminal, clock=lambda: NOW)
    notifier.begin()
    started = time.monotonic()
    notifier.end(0)
    held = time.monotonic() - started
    release.set()
    notifier.settle(5)
    assert held < 2.0
    assert terminal.getvalue() == ""
    assert updates.load_cache().attempted_at == NOW, "a lookup that was cut short is not retried at once"


def test_a_crash_in_the_lookup_is_not_the_commands_problem(enabled, monkeypatch):
    lookups(monkeypatch, RuntimeError("boom"))
    assert around("version") == ""


def test_a_cache_that_cannot_be_written_does_not_stop_the_command(enabled, monkeypatch, tmp_path):
    lookups(monkeypatch, NEWER)
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("ZEROTRACE_HOME", str(blocker / "home"))
    assert around("version") == ""


def test_a_broken_stream_does_not_stop_the_command(enabled, monkeypatch):
    lookups(monkeypatch, NEWER)

    class Broken(Terminal):
        def write(self, text):
            raise BrokenPipeError

    around("version", stream=Broken())


def test_version_mentions_an_update_on_a_terminal(enabled, at_a_terminal, capsys):
    updates.save_cache(Cache(checked_at=time.time(), attempted_at=time.time(), latest=NEWER))
    assert run("version") == 0
    captured = capsys.readouterr()
    assert "zerotrace" in captured.out
    assert SHOWN in captured.err


def test_a_blocked_commit_does_not_carry_the_notice(repo, enabled, at_a_terminal, capsys, fake):
    updates.save_cache(Cache(checked_at=time.time(), attempted_at=time.time(), latest=NEWER))
    write("pay.py", f'KEY = "{fake.stripe_live()}"\n')
    git("add", "-A")
    assert run("run") == 1
    assert SHOWN not in capsys.readouterr().err


def test_the_commit_hook_says_so_after_a_clean_commit(repo, enabled, at_a_terminal, capsys):
    updates.save_cache(Cache(checked_at=time.time(), attempted_at=time.time(), latest=NEWER))
    write("ok.py", "x = 1\n")
    git("add", "-A")
    assert run("run") == 0
    assert SHOWN in capsys.readouterr().err


# --- config and org policy -----------------------------------------------------------------------

def test_the_check_is_on_by_default(repo):
    assert cli.load_config().update_check is True


def test_a_repo_can_turn_it_off_for_itself(repo):
    write(repo / ".zerotrace.yml", "updates:\n  check: false\n")
    assert cli.load_config().update_check is False


def test_an_org_policy_can_lock_it_off(repo, git_env):
    write(git_env / "no-org-policy.yml", "updates:\n  check: false\nlocked: [updates.check]\n")
    write(repo / ".zerotrace.yml", "updates:\n  check: true\n")
    config = cli.load_config()
    assert config.update_check is False
    assert "updates.check" in config.locked


# --- `zerotrace doctor` --------------------------------------------------------------------------

def _update_row(update_check: bool = True):
    """The one row this feature adds to `zerotrace doctor`, without running every other check."""
    report = doctor.Report()
    doctor._check_update(report, Config(update_check=update_check))
    return report.rows[0]


def test_doctor_says_when_it_is_not_looking():
    row = _update_row()
    assert row.name == doctor.UPDATE
    assert row.status == doctor.OK
    assert "not checking" in row.result


def test_doctor_says_why_a_config_file_switched_it_off(enabled):
    row = _update_row(update_check=False)
    assert row.status == doctor.OK
    assert "updates.check" in row.result


def test_doctor_warns_when_a_newer_release_is_known(enabled):
    updates.save_cache(Cache(checked_at=time.time(), attempted_at=time.time(), latest=NEWER))
    row = _update_row()
    assert row.status == doctor.WARN
    assert NEWER in row.result
    assert "zerotrace update" in row.result


def test_doctor_confirms_the_latest_release(enabled):
    updates.save_cache(Cache(checked_at=time.time(), attempted_at=time.time(), latest=RUNNING))
    row = _update_row()
    assert row.status == doctor.OK
    assert "latest release" in row.result


def test_doctor_does_not_fail_over_a_lookup_it_could_not_make(enabled, monkeypatch):
    lookups(monkeypatch, UpdateError("offline"))
    row = _update_row()
    assert row.status == doctor.OK
    assert "could not tell" in row.result


def test_the_doctor_row_is_explained():
    assert doctor.UPDATE in doctor.ABOUT


def test_the_state_looks_up_when_a_day_has_passed(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWER)
    assert updates.state(True, now=NOW).kind == "available"
    assert asked.calls == 1


def test_the_state_uses_what_it_already_knows_within_the_day(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWEST)
    updates.save_cache(Cache(checked_at=NOW - 60, attempted_at=NOW - 60, latest=NEWER))
    assert updates.state(True, now=NOW).latest == NEWER
    assert asked.calls == 0


def test_the_state_when_it_cannot_tell(enabled, monkeypatch):
    lookups(monkeypatch, UpdateError("offline"))
    assert updates.state(True, now=NOW).kind == "unknown"


def test_the_state_when_nothing_newer_exists(enabled, monkeypatch):
    lookups(monkeypatch, RUNNING)
    assert updates.state(True, now=NOW).kind == "current"


def test_the_state_when_a_config_says_no(enabled, monkeypatch):
    asked = lookups(monkeypatch, NEWER)
    where = updates.state(False, now=NOW)
    assert where.kind == "off"
    assert "updates.check" in where.why
    assert asked.calls == 0


# --- `zerotrace update` --------------------------------------------------------------------------

class FakeSubprocess:
    """Stands in for the `subprocess` module as `updates` sees it: records, runs nothing."""
    DEVNULL = subprocess.DEVNULL

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.runs: list[tuple[list[str], dict]] = []
        self.spawned: list[tuple[list[str], dict]] = []

    def run(self, command, **kwargs):
        self.runs.append((command, kwargs))
        return SimpleNamespace(returncode=self.code)

    def Popen(self, command, **kwargs):      # noqa: N802 - the name is the real module's
        self.spawned.append((command, kwargs))


@pytest.fixture
def managed(enabled, tmp_path, monkeypatch):
    """An installer-made ZeroTrace home, and a subprocess that records instead of running."""
    home = tmp_path / "zt-home"
    (home / "venv").mkdir(parents=True)
    write(home / "install.log", "log\n")
    monkeypatch.setattr(updates, "detect_install", lambda: Install("installer", str(home)))
    monkeypatch.setattr(updates, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(updates, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/bash"))
    monkeypatch.setattr(updates, "_has_pii_engine", lambda: False)
    fake = FakeSubprocess()
    monkeypatch.setattr(updates, "subprocess", fake)
    return SimpleNamespace(home=home, subprocess=fake)


def test_check_says_a_newer_release_exists_and_installs_nothing(enabled, mirror, capsys):
    mirror.publish(NEWER)
    assert updates.update_command(check_only=True) == 0
    out = capsys.readouterr().out
    assert f"update available {RUNNING} -> {NEWER}" in out
    assert "zerotrace update" in out


def test_check_remembers_what_it_found(enabled, mirror):
    mirror.publish(NEWER)
    updates.update_command(check_only=True)
    assert updates.load_cache().latest == NEWER


def test_already_current_is_said_plainly(enabled, mirror, capsys):
    mirror.publish(RUNNING)
    assert updates.update_command() == 0
    assert "up to date" in capsys.readouterr().out


def test_a_lookup_that_fails_is_reported_and_fails(enabled, monkeypatch, capsys):
    lookups(monkeypatch, UpdateError("could not look up the latest release at x: offline"))
    assert updates.update_command() == 1
    assert "offline" in capsys.readouterr().err


def test_a_policy_that_locked_updates_off_is_obeyed(enabled, monkeypatch, capsys):
    asked = lookups(monkeypatch, NEWER)
    assert updates.update_command(locked_off=True) == 1
    assert "managed by your organisation" in capsys.readouterr().err
    assert asked.calls == 0


def test_the_cli_obeys_a_locked_policy(repo, enabled, monkeypatch, capsys):
    asked = lookups(monkeypatch, NEWER)
    write(enabled / "no-org-policy.yml", "updates:\n  check: false\nlocked: [updates.check]\n")
    assert run("update") == 1
    assert "managed by your organisation" in capsys.readouterr().err
    assert asked.calls == 0


def test_the_cli_checks(repo, enabled, monkeypatch, capsys):
    lookups(monkeypatch, NEWER)
    assert run("update", "--check") == 0
    assert SHOWN in capsys.readouterr().out


def test_update_is_a_command():
    assert "update" in cli.COMMANDS


def test_the_installer_for_this_release_is_verified_then_run(enabled, mirror, managed, monkeypatch):
    monkeypatch.setattr(updates, "_windows", lambda: False)
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT})
    assert updates.update_command() == 0
    command, options = managed.subprocess.runs[0]
    script = managed.home / "update" / INSTALL_SH
    assert command == ["/usr/bin/bash", str(script), "--version", f"v{NEWER}", "--no-model"]
    assert options["env"]["ZEROTRACE_HOME"] == str(managed.home)
    assert script.read_bytes() == SCRIPT


def test_the_ner_extra_is_asked_for_again_if_it_was_installed(enabled, mirror, managed, monkeypatch):
    monkeypatch.setattr(updates, "_windows", lambda: False)
    monkeypatch.setattr(updates, "_has_pii_engine", lambda: True)
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT})
    assert updates.update_command() == 0
    command, _ = managed.subprocess.runs[0]
    assert command[-1] == "--with-pii"


def test_a_failed_installer_is_reported_with_its_code_and_its_log(enabled, mirror, managed, monkeypatch, capsys):
    monkeypatch.setattr(updates, "_windows", lambda: False)
    managed.subprocess.code = 3
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT})
    assert updates.update_command() == 3
    err = capsys.readouterr().err
    assert "exited with code 3" in err
    assert "install.log" in err


def test_a_tampered_installer_is_never_run(enabled, mirror, managed, monkeypatch, capsys):
    monkeypatch.setattr(updates, "_windows", lambda: False)
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT}, sums={INSTALL_SH: "0" * 64})
    assert updates.update_command() == 1
    assert managed.subprocess.runs == []
    assert "does not match" in capsys.readouterr().err


def test_without_bash_it_says_so(enabled, mirror, managed, monkeypatch, capsys):
    monkeypatch.setattr(updates, "_windows", lambda: False)
    monkeypatch.setattr(updates, "shutil", SimpleNamespace(which=lambda name: None))
    mirror.publish(NEWER, {INSTALL_SH: SCRIPT})
    assert updates.update_command() == 1
    assert "bash" in capsys.readouterr().err
    assert managed.subprocess.runs == []


@pytest.mark.parametrize("terminal", [True, False])
def test_on_windows_the_installer_starts_in_its_own_powershell_after_this_one_exits(
        enabled, mirror, managed, monkeypatch, terminal):
    monkeypatch.setattr(updates, "_windows", lambda: True)
    monkeypatch.setattr(updates, "_interactive", lambda: terminal)
    mirror.publish(NEWER, {INSTALL_PS1: SCRIPT})
    assert updates.update_command() == 0
    command, options = managed.subprocess.spawned[0]
    script = command[command.index("-Command") + 1]
    assert command[0] == "powershell.exe"
    assert f"Wait-Process -Id {os.getpid()}" in script
    assert f"& '{managed.home / 'update' / INSTALL_PS1}'" in script
    assert f"-Version 'v{NEWER}' -NoModel" in script
    assert options["env"]["ZEROTRACE_HOME"] == str(managed.home)
    assert managed.subprocess.runs == [], "the installer cannot be a child: it replaces this very environment"


def test_a_window_is_made_only_for_a_person_who_is_there_to_see_it(enabled, mirror, managed, monkeypatch):
    monkeypatch.setattr(updates, "_windows", lambda: True)
    mirror.publish(NEWER, {INSTALL_PS1: SCRIPT})
    monkeypatch.setattr(updates, "_interactive", lambda: True)
    updates.update_command()
    monkeypatch.setattr(updates, "_interactive", lambda: False)
    updates.update_command()
    seen, unseen = managed.subprocess.spawned
    assert seen[1]["creationflags"] == updates._NEW_CONSOLE
    assert "Read-Host" in seen[0][-1]
    assert unseen[1]["creationflags"] == updates._NO_WINDOW
    assert unseen[1]["stdout"] == subprocess.DEVNULL
    assert "Read-Host" not in unseen[0][-1]


def test_a_powershell_that_cannot_start_is_reported(enabled, mirror, managed, monkeypatch, capsys):
    monkeypatch.setattr(updates, "_windows", lambda: True)
    monkeypatch.setattr(updates, "_interactive", lambda: False)
    mirror.publish(NEWER, {INSTALL_PS1: SCRIPT})

    def refuse(command, **kwargs):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(managed.subprocess, "Popen", refuse)
    assert updates.update_command() == 1
    assert "could not start PowerShell" in capsys.readouterr().err


def test_a_path_with_an_apostrophe_cannot_break_out_of_its_quotes():
    script = updates.powershell_script(42, r"C:\Users\O'Brien\.zerotrace\update\install.ps1",
                                       ["-Version", "v0.5.0", "-NoModel"], pause=False)
    assert r"& 'C:\Users\O''Brien\.zerotrace\update\install.ps1'" in script
    assert script.endswith("exit $code")


@pytest.mark.parametrize(("windows", "pii", "expected"), [
    (False, False, ["--version", "v0.5.0", "--no-model"]),
    (False, True, ["--version", "v0.5.0", "--no-model", "--with-pii"]),
    (True, False, ["-Version", "v0.5.0", "-NoModel"]),
    (True, True, ["-Version", "v0.5.0", "-NoModel", "-WithPii"]),
])
def test_the_installer_is_asked_for_exactly_that_release(windows, pii, expected):
    assert updates.installer_args("v0.5.0", pii, windows) == expected


def test_the_flags_update_passes_are_ones_the_installers_accept():
    """If an installer renames or drops a flag, `zerotrace update` would start failing on a
    release that was never exercised by a person - so this reads both installers."""
    shell = (ROOT / INSTALL_SH).read_text(encoding="utf-8")
    powershell = (ROOT / INSTALL_PS1).read_text(encoding="ascii")
    for flag in updates.installer_args("v0.5.0", True, windows=False):
        if flag.startswith("--"):
            assert f"{flag})" in shell, f"install.sh has no {flag} case"
    for flag in updates.installer_args("v0.5.0", True, windows=True):
        if flag.startswith("-"):
            assert f"${flag[1:]}" in powershell, f"install.ps1 has no {flag} parameter"
    assert "ZEROTRACE_HOME" in shell
    assert "ZEROTRACE_HOME" in powershell


@pytest.mark.parametrize(("kind", "words"), [
    ("binary", "cannot replace itself"),
    ("source", "source checkout"),
    ("other", "not put here by the ZeroTrace installer"),
])
def test_a_copy_the_installer_did_not_make_is_not_replaced_from_here(
        enabled, mirror, monkeypatch, capsys, kind, words):
    mirror.publish(NEWER)
    fake = FakeSubprocess()
    monkeypatch.setattr(updates, "subprocess", fake)
    monkeypatch.setattr(updates, "detect_install", lambda: Install(kind))
    assert updates.update_command() == 1
    assert words in capsys.readouterr().err
    assert fake.runs == []
    assert fake.spawned == []


def test_the_standalone_binary_is_pointed_at_the_release_page(enabled, mirror, monkeypatch, capsys):
    mirror.publish(NEWER)
    monkeypatch.setattr(updates, "detect_install", lambda: Install("binary"))
    updates.update_command()
    assert f"{mirror.repo}/releases/tag/v{NEWER}" in capsys.readouterr().err


def test_a_copy_made_by_the_installer_is_recognised(tmp_path, monkeypatch):
    home = tmp_path / ".zerotrace"
    (home / "venv").mkdir(parents=True)
    write(home / "install.log", "log\n")
    monkeypatch.setattr(sys, "prefix", str(home / "venv"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert updates.detect_install() == Install("installer", str(home))


def test_a_venv_somewhere_else_is_not_the_installers(tmp_path, monkeypatch):
    (tmp_path / "venv").mkdir()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(updates, "__file__", str(tmp_path / "venv" / "lib" / "site-packages" / "zerotrace" / "updates.py"))
    assert updates.detect_install().kind == "other"


def test_a_source_checkout_is_recognised(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(updates, "__file__", str(tmp_path / "src" / "zerotrace" / "updates.py"))
    assert updates.detect_install().kind == "source"


def test_a_frozen_binary_is_recognised(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert updates.detect_install().kind == "binary"

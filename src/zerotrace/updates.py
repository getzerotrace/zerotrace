"""Knowing that a newer ZeroTrace exists, and moving to it.

Two features share this module and one rule: nothing here ever stands between a person and
their commit.

  * the notice - after a command that succeeded, one line on stderr when a newer release is
    known: `zerotrace: update available 0.4.0 → 0.5.0 · run zerotrace update`. It is printed
    only where a person can read it (stderr is a terminal, and this is not CI) and the answer
    comes from a lookup that runs *beside* the command, at most once a day. A hook never waits
    on the network for more than a second, and only on the day a lookup is due: if it has not
    finished by then, the answer arrives with a later command.
  * `zerotrace update` - fetches the installer attached to the latest release, checks it
    against that release's SHA256SUMS, and runs it. The installer verifies the wheel and every
    dependency the same way, so there is exactly one way ZeroTrace gets onto a machine and an
    update is that way again, not a second code path that can disagree with it.

What leaves the machine, in full: one HEAD request a day to `<repository>/releases/latest`,
which GitHub answers with a redirect that names the newest tag (so no API token and no rate
limit), carrying a user agent of `zerotrace/<version>`. A repository, a path, a user name or a
finding is never part of it, and detection itself still never touches the network. It is off
with `ZEROTRACE_NO_UPDATE_CHECK=1`, `NO_UPDATE_NOTIFIER=1`, in CI, or with `updates: {check:
false}` in any config layer - which an org policy can lock (docs/POLICY.md).
"""
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from . import __version__
from .config import load_config, zerotrace_home

REPO_URL = "https://github.com/getzerotrace/zerotrace"
CHECK_INTERVAL = 24 * 60 * 60        # a newer release is looked for once a day...
RETRY_INTERVAL = 60 * 60             # ...and an offline machine is not asked again for an hour
NOTICE_INTERVAL = 24 * 60 * 60       # a hook mentions the same release once a day
LOOKUP_TIMEOUT = 3.0
DOWNLOAD_TIMEOUT = 30.0
SETTLE_SECONDS = 1.0                 # the most a finished command waits for the lookup beside it
SUMS_LIMIT = 256 * 1024
INSTALLER_LIMIT = 4 * 1024 * 1024

# The commands that may mention an update. `run` is the commit hook: it says so at most once a
# day. A command somebody typed says so every time. Not here: the hooks that run on every push
# and commit, `gateway` and `eval` (their output is read by programs), `doctor` (it has a row
# of its own), and `update` itself.
HOOK_COMMANDS = frozenset({"run"})
NOTICE_COMMANDS = HOOK_COMMANDS | {"version", "scan", "init", "model", "exceptions", "ui"}

_OPT_OUT_ENV = ("ZEROTRACE_NO_UPDATE_CHECK", "NO_UPDATE_NOTIFIER", "CI")
_FALSY = frozenset({"0", "false", "no", "off"})
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)\Z")
_TAG_URL = re.compile(r"/releases/tag/v(\d+\.\d+\.\d+)\Z")
_SUMS_LINE = re.compile(r"([0-9a-fA-F]{64})\s+\*?(\S+)\Z")
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class UpdateError(Exception):
    """Finding or fetching a release went wrong; the message says what and what was not done."""


# --- versions ---------------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...] | None:
    """`0.5.0` or `v0.5.0` as (0, 5, 0); None for anything that is not a release number."""
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate: str, current: str) -> bool:
    wanted, have = parse_version(candidate), parse_version(current)
    return wanted is not None and have is not None and wanted > have


# --- the network: GitHub's release pages, over https, and nothing else ---------------------------

def repo_url() -> str:
    """Where releases live. `ZEROTRACE_REPO_URL` is the variable both installers already read."""
    return (os.environ.get("ZEROTRACE_REPO_URL") or REPO_URL).strip().rstrip("/")


def _checked_url(url: str) -> str:
    """https only. A mirror on this very machine may use plain http (that is how the tests and a
    local release mirror work); nothing else may, so a hostile redirect cannot downgrade it."""
    parts = urlsplit(url)
    secure = parts.scheme == "https" and bool(parts.hostname)
    local = parts.scheme == "http" and parts.hostname in _LOOPBACK
    if not (secure or local):
        raise UpdateError(f"refusing {url}: releases are fetched over https only")
    return url


def _tls_context():
    """The system's certificate store - and certifi's bundle only when that store is empty.

    A single-file binary or a python.org build on macOS can have no store to read, and then
    every lookup would fail for a reason nobody can see. certifi is always installed (it is a
    dependency of the requests that detect-secrets needs), but loading its bundle is not free,
    and a machine with a store - a company root included - should be trusted as it is.
    """
    import ssl
    context = ssl.create_default_context()
    if not context.cert_store_stats().get("x509_ca"):
        with contextlib.suppress(ImportError, OSError):
            import certifi
            context.load_verify_locations(cafile=certifi.where())
    return context


def _open(url: str, timeout: float, method: str = "GET"):
    # Imported here: it brings in ssl and http, which a hook that never looks anything up
    # should not pay for on every commit.
    import urllib.request

    class HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _checked_url(newurl)
            redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
            if redirected is not None:
                redirected.method = req.get_method()    # a HEAD stays a HEAD across the hop
            return redirected

    handlers: list = [HttpsOnly]
    if url.startswith("https:"):
        handlers.append(urllib.request.HTTPSHandler(context=_tls_context()))
    request = urllib.request.Request(_checked_url(url), method=method,
                                     headers={"User-Agent": f"zerotrace/{__version__}"})
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def _reason(exc: Exception) -> str:
    return str(getattr(exc, "reason", None) or exc)


def fetch_latest(timeout: float = LOOKUP_TIMEOUT) -> str:
    """The newest published release as a bare version (`0.5.0`).

    `/releases/latest` redirects to `/releases/tag/vX.Y.Z`; the tag is in the final URL, so the
    request carries no body to read and counts against no API rate limit.
    """
    url = f"{repo_url()}/releases/latest"
    try:
        with _open(url, timeout, "HEAD") as response:
            final = response.geturl()
    except UpdateError:
        raise
    except Exception as exc:     # timeouts, DNS, TLS, a proxy's error page: all "could not look"
        raise UpdateError(f"could not look up the latest release at {repo_url()}: "
                          f"{_reason(exc)}") from exc
    match = _TAG_URL.search(final)
    if match is None:
        raise UpdateError(f"{repo_url()} has no published release")
    return match.group(1)


def download(url: str, limit: int, timeout: float = DOWNLOAD_TIMEOUT) -> bytes:
    """A small file, refused if it is larger than `limit`."""
    try:
        with _open(url, timeout) as response:
            data = response.read(limit + 1)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"could not download {url}: {_reason(exc)}") from exc
    if len(data) > limit:
        raise UpdateError(f"{url} is larger than {limit} bytes; refusing it")
    return data


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output ("<hash>  <name>", or "<hash> *<name>") as {name: hash}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        match = _SUMS_LINE.match(line.strip())
        if match:
            sums.setdefault(match.group(2), match.group(1).lower())
    return sums


def fetch_installer(tag: str, directory: Path, name: str) -> Path:
    """Download `name` from release `tag` into `directory`, only if it is what that release's
    SHA256SUMS says it is. Nothing is run here; a file that does not verify is not written."""
    base = f"{repo_url()}/releases/download/{tag}"
    sums = parse_sums(download(f"{base}/SHA256SUMS", SUMS_LIMIT).decode("utf-8", errors="replace"))
    want = sums.get(name)
    if want is None:
        raise UpdateError(f"release {tag} does not list {name} in its SHA256SUMS; not running it")
    data = download(f"{base}/{name}", INSTALLER_LIMIT)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise UpdateError(f"{name} from {tag} does not match its SHA256SUMS entry (expected "
                          f"{want}, got {got}); nothing was run")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


# --- what was last learned, kept in ~/.zerotrace/update-check.json -------------------------------

@dataclass
class Cache:
    checked_at: float = 0.0       # the last lookup that got an answer
    attempted_at: float = 0.0     # the last lookup, answered or not
    latest: str = ""              # the newest release it found, e.g. "0.5.0"
    notified_at: float = 0.0      # when a hook last mentioned `notified_for`
    notified_for: str = ""

    def newer(self) -> str:
        """The release this machine knows of that is newer than the one running, or ""."""
        return self.latest if is_newer(self.latest, __version__) else ""


def _cache_path() -> Path:
    return Path(zerotrace_home()) / "update-check.json"


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _release(value: object) -> str:
    return value if isinstance(value, str) and parse_version(value) is not None else ""


def load_cache() -> Cache:
    """What the last lookups found. A missing, unreadable or hand-edited file is just empty."""
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Cache()
    if not isinstance(raw, dict):
        return Cache()
    return Cache(checked_at=_number(raw.get("checked_at")),
                 attempted_at=_number(raw.get("attempted_at")),
                 latest=_release(raw.get("latest")),
                 notified_at=_number(raw.get("notified_at")),
                 notified_for=_release(raw.get("notified_for")))


def save_cache(cache: Cache) -> None:
    """Atomically, so a lookup and a command running at the same time cannot corrupt it. A
    directory that cannot be written costs the notice, never the command."""
    path = _cache_path()
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(asdict(cache)), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()


def _age(then: float, now: float) -> float:
    """Seconds since `then`; a timestamp from the future (a clock that was set back) is old."""
    return now - then if then <= now else float("inf")


def due(cache: Cache, now: float) -> bool:
    """Is a lookup owed: the last answer is a day old and the last try was not just now."""
    return _age(cache.attempted_at, now) >= RETRY_INTERVAL and _age(cache.checked_at, now) >= CHECK_INTERVAL


def remember(latest: str, now: float) -> Cache:
    cache = load_cache()
    cache.latest, cache.checked_at = latest, now
    save_cache(cache)
    return cache


def refresh(now: float | None = None, timeout: float = LOOKUP_TIMEOUT) -> Cache:
    """One lookup, remembered. A failed one is remembered as an attempt only. Never raises."""
    now = time.time() if now is None else now
    cache = load_cache()
    cache.attempted_at = now
    with contextlib.suppress(UpdateError):
        cache.latest, cache.checked_at = fetch_latest(timeout), now
    save_cache(cache)
    return cache


# --- when a lookup or a notice is allowed -----------------------------------------------------------

def opt_out_reason(env: Mapping[str, str] | None = None) -> str:
    """Why this environment turns update checks off, or "" when it does not."""
    env = os.environ if env is None else env
    for name in _OPT_OUT_ENV:
        value = env.get(name, "").strip().lower()
        if value and value not in _FALSY:
            return f"{name} is set"
    return ""


def _is_terminal(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _config_allows() -> bool:
    """`updates.check` in the merged config. When the config cannot be read, stay quiet."""
    try:
        return load_config().update_check
    except Exception:
        return False


def print_notice(latest: str, stream: TextIO | None = None) -> None:
    from rich.console import Console
    from rich.text import Text

    from .ui import glyphs, theme

    class Notice(Console):
        def on_broken_pipe(self) -> None:
            # Rich's answer to a closed pipe is to exit the process with status 1 - which would
            # turn a passing commit into a refused one over a line of advice.
            raise OSError("the stream this notice was meant for is closed")

    console = Notice(file=sys.stderr if stream is None else stream, highlight=False)
    marks = glyphs.for_console(console)
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("zerotrace: ", style="dim")
    line.append("update available ", style=theme.ACCENT_BOLD)
    line.append(f"{__version__} {marks['arrow']} {latest}")
    line.append(f" {marks['dot']} run ", style="dim")
    line.append("zerotrace update", style="bold")
    console.print(line)


class Notifier:
    """Wraps one command: starts the daily lookup beside it, mentions the result after it.

    Nothing it does can fail the command or slow it down: every step is guarded, the lookup
    is a daemon thread, and the cache is read once and written only when there is news.
    """

    def __init__(self, command: str, stream: TextIO | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.command = command
        self.stream = sys.stderr if stream is None else stream
        self._clock = clock
        self._worker: threading.Thread | None = None
        self._permitted: bool | None = None

    def _eligible(self) -> bool:
        return (self.command in NOTICE_COMMANDS and not opt_out_reason()
                and _is_terminal(self.stream))

    def _allowed(self) -> bool:
        """The config is read the first time it matters: most commands never get that far."""
        if self._permitted is None:
            self._permitted = _config_allows()
        return self._permitted

    def _lookup(self) -> None:
        with contextlib.suppress(Exception):
            remember(fetch_latest(), self._clock())

    def begin(self) -> None:
        with contextlib.suppress(Exception):
            self._begin()

    def _begin(self) -> None:
        if not self._eligible():
            return
        cache, now = load_cache(), self._clock()
        if not due(cache, now) or not self._allowed():
            return
        cache.attempted_at = now           # before the lookup, so a killed one is not retried at once
        save_cache(cache)
        self._worker = threading.Thread(target=self._lookup, name="zerotrace-update-check", daemon=True)
        self._worker.start()

    def end(self, code: int) -> None:
        with contextlib.suppress(Exception, KeyboardInterrupt):
            self._end(code)

    def settle(self, seconds: float | None = None) -> None:
        """Give the lookup that is running beside the command a moment to finish."""
        if self._worker is not None:
            self._worker.join(SETTLE_SECONDS if seconds is None else seconds)

    def _should_say(self, cache: Cache, latest: str) -> bool:
        if self.command not in HOOK_COMMANDS:
            return True
        return cache.notified_for != latest or _age(cache.notified_at, self._clock()) >= NOTICE_INTERVAL

    def _end(self, code: int) -> None:
        if code != 0 or not self._eligible():
            return
        self.settle()
        cache = load_cache()
        latest = cache.newer()
        if not latest or not self._should_say(cache, latest) or not self._allowed():
            return
        print_notice(latest, self.stream)
        cache.notified_at, cache.notified_for = self._clock(), latest
        save_cache(cache)


# --- `zerotrace doctor` ---------------------------------------------------------------------------

@dataclass(frozen=True)
class State:
    kind: str          # off | unknown | current | available
    latest: str = ""
    why: str = ""


def state(update_check: bool, now: float | None = None) -> State:
    """Where this install stands, for the doctor: looks up once a day, as a typed command may."""
    why = opt_out_reason() or ("" if update_check else "updates.check is false in a config file")
    if why:
        return State("off", why=why)
    now = time.time() if now is None else now
    cache = load_cache()
    if due(cache, now):
        cache = refresh(now)
    if cache.newer():
        return State("available", cache.latest)
    return State("current" if cache.latest else "unknown")


# --- `zerotrace update` ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Install:
    kind: str          # installer | binary | source | other
    home: str = ""     # the ZeroTrace home of an installer-managed install


def _windows() -> bool:
    return sys.platform == "win32"


def detect_install() -> Install:
    """How this copy of ZeroTrace got here - which decides whether it can replace itself."""
    if getattr(sys, "frozen", False):
        return Install("binary")
    prefix = Path(sys.prefix)
    home = prefix.parent
    # The installers build `<home>/venv` and leave `install.log` beside it.
    if prefix.name == "venv" and (home / "install.log").is_file():
        return Install("installer", str(home))
    if not any(part in ("site-packages", "dist-packages") for part in Path(__file__).parts):
        return Install("source")
    return Install("other")


def installer_args(tag: str, pii: bool, windows: bool) -> list[str]:
    """What `zerotrace update` asks the installer for: exactly the release it verified, no model
    pull (the model is already where the person put it), and the NER extra if they had it."""
    if windows:
        return ["-Version", tag, "-NoModel", *(["-WithPii"] if pii else [])]
    return ["--version", tag, "--no-model", *(["--with-pii"] if pii else [])]


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell_script(pid: int, script: str, args: list[str], pause: bool) -> str:
    """Wait for this process to end, then run the installer.

    The installer replaces the environment this process is running from, and Windows will not
    move a directory with a running program in it - so the installer cannot be this process's
    child. It is started in its own PowerShell, which first waits for this one to exit.
    """
    call = " ".join(["&", _ps_quote(script),
                     *(arg if arg.startswith("-") else _ps_quote(arg) for arg in args)])
    steps = [f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue", "Start-Sleep -Seconds 1",
             call, "$code = $LASTEXITCODE"]
    if pause:
        steps += ["Write-Host ''", "Read-Host 'zerotrace: press Enter to close this window' | Out-Null"]
    steps.append("exit $code")
    return "; ".join(steps)


def _powershell() -> str:
    """Windows PowerShell by absolute path: present on every Windows, and not found via PATH."""
    candidate = os.path.join(os.environ.get("SYSTEMROOT") or r"C:\Windows", "System32",
                             "WindowsPowerShell", "v1.0", "powershell.exe")
    return candidate if os.path.exists(candidate) else shutil.which("powershell.exe") or candidate


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _has_pii_engine() -> bool:
    """Was the optional Presidio engine installed? The installer rebuilds the environment from
    scratch, so an update that did not ask for it again would quietly take it away."""
    return importlib.util.find_spec("presidio_analyzer") is not None


def _hand_off_to_powershell(script: Path, args: list[str], install: Install) -> int:
    pause = _interactive()
    command = [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               powershell_script(os.getpid(), str(script), args, pause)]
    # With a terminal, the installer gets a window of its own to show its progress in. Without
    # one (a script, a scheduled task) it runs unseen and must not hold this process's pipes.
    options: dict = {"creationflags": _NEW_CONSOLE} if pause else {
        "creationflags": _NO_WINDOW, "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        subprocess.Popen(command, env={**os.environ, "ZEROTRACE_HOME": install.home}, **options)
    except OSError as exc:
        print(f"zerotrace: could not start PowerShell to run the installer: {exc}", file=sys.stderr)
        return 1
    where = "in a new window" if pause else "in the background"
    print(f"zerotrace: the installer starts {where} as soon as this command exits. "
          f"Its log: {os.path.join(install.home, 'install.log')}")
    return 0


def _run_installer(script: Path, args: list[str], install: Install) -> int:
    bash = shutil.which("bash")
    if bash is None:
        print("zerotrace: bash is needed to run the installer and was not found.", file=sys.stderr)
        return 1
    env = {**os.environ, "ZEROTRACE_HOME": install.home}
    code = subprocess.run([bash, str(script), *args], env=env, check=False).returncode
    # The installer has just replaced the environment this is running from: say what happened
    # with what is already loaded, and import nothing more.
    if code != 0:
        print(f"zerotrace: the installer exited with code {code}. The previous install was "
              f"kept; its log is {os.path.join(install.home, 'install.log')}", file=sys.stderr)
    return code


def _guidance(install: Install, latest: str) -> str:
    """What to do instead, for a copy of ZeroTrace that is not one the installer made."""
    page = f"{repo_url()}/releases/tag/v{latest}"
    if install.kind == "binary":
        return (f"zerotrace: this is the standalone binary ({sys.executable}); it cannot replace "
                f"itself. Download the new one from {page} and put it in its place.")
    if install.kind == "source":
        return ("zerotrace: this is a source checkout. Update it with git, then run its installer "
                "(./install.sh, or .\\install.ps1 on Windows) from the checkout.")
    one_liner = ("irm https://getzerotrace.github.io/zerotrace/install.ps1 | iex" if _windows()
                 else "curl -fsSL https://getzerotrace.github.io/zerotrace/install.sh | bash")
    return ("zerotrace: this copy was not put here by the ZeroTrace installer (pip or pipx?), "
            "so it is not replaced from here. Update it the way it was installed, or run the "
            f"installer, which builds an environment of its own: {one_liner}")


def _install(latest: str) -> int:
    install = detect_install()
    if install.kind != "installer":
        print(_guidance(install, latest), file=sys.stderr)
        return 1
    tag, windows = f"v{latest}", _windows()
    try:
        script = fetch_installer(tag, Path(install.home) / "update",
                                 "install.ps1" if windows else "install.sh")
    except UpdateError as exc:
        print(f"zerotrace: {exc}", file=sys.stderr)
        return 1
    print(f"zerotrace: installing {latest} with the installer from release {tag}, "
          "checked against that release's SHA256SUMS")
    args = installer_args(tag, _has_pii_engine(), windows)
    return (_hand_off_to_powershell if windows else _run_installer)(script, args, install)


def update_command(check_only: bool = False, locked_off: bool = False) -> int:
    """`zerotrace update`: say whether a newer release exists and, unless asked only to check,
    install it. `locked_off` is an org policy that fixed `updates.check` to false: the
    administrator owns the installed version, so neither looking nor installing happens."""
    if locked_off:
        print("zerotrace: updates are managed by your organisation (updates.check is locked "
              "off in its policy); ask your administrator.", file=sys.stderr)
        return 1
    try:
        latest = fetch_latest()
    except UpdateError as exc:
        print(f"zerotrace: {exc}", file=sys.stderr)
        return 1
    remember(latest, time.time())
    if not is_newer(latest, __version__):
        print(f"zerotrace {__version__} is up to date (latest release: {latest}).")
        return 0
    print(f"zerotrace: update available {__version__} -> {latest}")
    if check_only:
        print("zerotrace: run `zerotrace update` to install it.")
        return 0
    return _install(latest)

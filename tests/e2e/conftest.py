"""Fixtures for the end-to-end flows: a machine in a box.

Everything here runs the real thing - the real install.sh, a real virtualenv, a real git
installation, real hooks, a real `git commit` - inside a sandboxed HOME with its own global
git config. Nothing touches the developer's own machine, and no test needs a Docker daemon
(`fake_docker` from the parent conftest plays every Docker state).

The installer flows build a virtualenv, so they only run when asked:

    ZEROTRACE_E2E=1 .venv/bin/python -m pytest -q tests/e2e

CI runs exactly that, on Linux, macOS and Windows.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"

# Building a venv and resolving a lock is seconds, not milliseconds, and it needs the network
# unless a wheelhouse is around. An opt-in keeps `pytest -q` the fast inner loop it is.
requires_e2e = pytest.mark.skipif(
    not os.environ.get("ZEROTRACE_E2E"),
    reason="set ZEROTRACE_E2E=1 to run the installer flows (they build a real virtualenv)")

posix_only = pytest.mark.skipif(os.name == "nt",
                                reason="install.sh is the POSIX installer; install.ps1 has "
                                       "its own flows in this directory")

windows_only = pytest.mark.skipif(os.name != "nt", reason="install.ps1 needs Windows")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fresh machine: its own HOME, its own global git config, nothing of ours in it."""
    home = tmp_path / "machine"
    (home / "bin").mkdir(parents=True)
    # The real environment, with only what must be isolated replaced. Building one from a
    # handful of keys instead looked tidier and was wrong: on Windows it left out PATHEXT, so
    # `Get-Command git` found nothing and the installer correctly refused to run - a whole
    # platform's tests failing over an environment variable the test forgot to pass on.
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "ZEROTRACE_HOME": str(home / ".zerotrace"),
        "ZEROTRACE_BIN_DIR": str(home / "bin"),
        "TERM": "dumb",                      # one line per step, no carriage returns to parse
        # Pin the width: rich wraps its tables to the console, and the Windows runner's
        # console is a column narrower than Linux's - an assertion on a sentence would then
        # pass on one OS and fail on another for no reason anyone can see.
        "COLUMNS": "200",
        # Nothing listens there, so an install never waits on a model it was not asked for.
        "ZEROTRACE_MODEL_ENDPOINT": "http://127.0.0.1:9",
        # A container that never answers is a real case; waiting the real three minutes for it
        # in a test is not.
        "ZEROTRACE_MODEL_WAIT_SECONDS": "3",
    }
    # Whatever the developer running this has set for themselves must not decide the outcome.
    # PSModulePath is in here for a specific reason: the CI step runs under PowerShell 7, whose
    # module path Windows PowerShell 5.1 cannot use - inheriting it made `Get-FileHash`
    # "not recognized" inside the installer. Unset, each PowerShell computes its own.
    for leak in ("ZEROTRACE_POLICY", "ZEROTRACE_EXTRAS", "ZEROTRACE_REF", "ZEROTRACE_ASCII",
                 "ZEROTRACE_NO_MODIFY_PATH", "ZEROTRACE_LOGO", "NO_COLOR", "VIRTUAL_ENV",
                 "PSModulePath"):
        env.pop(leak, None)
    return Machine(home, env)


# Box-drawing characters, and the ASCII fallbacks a legacy console gets.
_BORDERS = str.maketrans({character: " " for character in "│┃║|╭╮╰╯┌┐└┘├┤┬┴┼─━═+"})


def flat(text: str) -> str:
    """Text with table borders and every run of whitespace removed.

    A sentence printed inside a table is wrapped to the console width and each line is fenced
    by borders, so "a staged credential was refused by the pre-commit hook" arrives as two
    lines with a `│` between them. Assertions are about what was said, not about where the
    renderer happened to break it - and the width differs per OS, which is how this first
    showed up as a Linux-only failure.
    """
    return " ".join(text.translate(_BORDERS).split())


class Machine:
    def __init__(self, home: Path, env: dict):
        self.home = home
        self.env = env

    @property
    def zerotrace_home(self) -> Path:
        return Path(self.env["ZEROTRACE_HOME"])

    @property
    def bin_dir(self) -> Path:
        return Path(self.env["ZEROTRACE_BIN_DIR"])

    @property
    def launcher(self) -> Path:
        """What ends up on PATH: a shell shim, or a .cmd that cmd.exe and PowerShell can run."""
        return self.bin_dir / ("zerotrace.cmd" if os.name == "nt" else "zerotrace")

    @property
    def uninstaller(self) -> Path:
        return self.bin_dir / ("zerotrace-uninstall.cmd" if os.name == "nt"
                               else "zerotrace-uninstall")

    def install(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
        if os.name == "nt":
            done = self.run("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(INSTALL_PS1), *args)
        else:
            done = self.run("bash", str(INSTALL_SH), *args)
        assert done.returncode == expect, f"install {args}\n{done.stdout}\n{done.stderr}"
        return done

    def run(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(list(args), env={**self.env, **kwargs.pop("extra_env", {})},
                              capture_output=True, text=True, timeout=1800, check=False,
                              cwd=str(self.home), **kwargs)

    def zerotrace(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(str(self.launcher), *args)

    def git_global(self, key: str) -> str:
        return self.run("git", "config", "--global", "--get", key).stdout.strip()

    def hooks_path(self) -> Path | None:
        """core.hooksPath as a Path.

        git answers with forward slashes even on Windows, so comparing the raw string to one
        built by pathlib fails on a machine where the install is perfectly fine. A Path knows
        the two spellings are the same place.
        """
        value = self.git_global("core.hooksPath")
        return Path(value) if value else None

    def rc_files(self) -> list[Path]:
        return [self.home / name for name in (".bashrc", ".zshrc", ".profile")]

    def marker_lines(self) -> int:
        total = 0
        for rc in self.rc_files():
            if rc.exists():
                total += rc.read_text(encoding="utf-8").count("added by ZeroTrace installer")
        return total


@pytest.fixture(scope="session")
def release_assets(tmp_path_factory) -> Path:
    """A directory shaped exactly like a published release: the wheel, the hash-locked
    requirements and SHA256SUMS. What `install.sh --from` and the release job both consume."""
    import hashlib

    out = tmp_path_factory.mktemp("release")
    build = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation",
                            "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True,
                           check=False)
    if build.returncode != 0:
        pytest.skip(f"cannot build a wheel here: {build.stderr[-400:]}")
    shutil.copyfile(ROOT / "requirements" / "install.txt", out / "requirements-install.txt")
    lines = []
    for path in sorted(out.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out

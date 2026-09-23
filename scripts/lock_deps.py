"""Regenerate the requirements/*.txt locks: the exact, hash-pinned packages CI and contributors
install.

    python scripts/lock_deps.py              # keep current pins, add or drop what changed
    python scripts/lock_deps.py --upgrade    # move every pin to the newest allowed version

Needs `uv` (`pipx install uv`, `brew install uv`, or `pip install uv`).

Why lock files: every install runs `pip install --require-hashes --only-binary :all: -r <lock>`,
so each package is a file whose hash is known in advance and nothing runs setup code while it
installs (wheels only). `--universal` resolves once for every OS and Python version the project
supports, so Windows-only dependencies such as colorama are in the file even when it is
generated on a Mac.

    runtime.txt   the package's own dependencies + the build backend (secret-scan, dogfood,
                  package jobs)
    dev.txt       + the dev and tui extras (tests, lint, type-check, SonarQube, contributors)
    release.txt   + PyInstaller (the release binaries)

The locks are `.txt` so GitHub's dependency graph and Dependabot read them: alerts then cover
the versions CI really installs, not just the lower bounds in pyproject.toml. No `.in` file
shares a name with a lock, so nothing mistakes a pair of them for a pip-compile set.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# lock file -> (extras of the project, extra input files)
LOCKS = {
    "runtime.txt": ([], ["build.in"]),
    # What install.sh / install.ps1 put in a user's environment: the guardrail, the
    # full-screen apps, and the build backend (a clone install builds the wheel itself).
    "install.txt": (["tui"], ["build.in"]),
    "dev.txt": (["dev"], ["build.in"]),
    "release.txt": (["dev"], ["build.in", "pyinstaller.in"]),
}
LOWEST_PYTHON = "3.11"          # keep in step with requires-python in pyproject.toml


def compile_lock(uv: str, name: str, extras: list[str], inputs: list[str], upgrade: bool) -> None:
    command = [uv, "pip", "compile", "pyproject.toml",
               *(f"requirements/{source}" for source in inputs),
               *(arg for extra in extras for arg in ("--extra", extra)),
               "--universal", "--generate-hashes", "--python-version", LOWEST_PYTHON,
               "--custom-compile-command", "python scripts/lock_deps.py",
               "--quiet", "--output-file", f"requirements/{name}"]
    if upgrade:
        command.append("--upgrade")
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upgrade", action="store_true", help="move every pin to the newest version")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        print("lock_deps: `uv` is not installed (pipx install uv, or pip install uv)", file=sys.stderr)
        return 1
    for name, (extras, inputs) in LOCKS.items():
        compile_lock(uv, name, extras, inputs, args.upgrade)
        print(f"lock_deps: wrote requirements/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

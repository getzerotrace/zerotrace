"""Render a demo repo from demo/fixtures/<name>, generating format-valid FAKE credentials.

Used by both run_demo.sh and run_demo.ps1 so the two demos can never drift apart, and so
this repository never contains a realistic-looking secret itself.

    python demo/render_fixtures.py payments-api /tmp/zerotrace-demo/payments-api [--fresh]
"""
import os
import re
import secrets
import shutil
import string
import sys

ALNUM = string.ascii_letters + string.digits
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _r(n: int, alphabet: str = ALNUM) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _password() -> str:
    return _r(4, string.ascii_uppercase) + _r(6) + secrets.choice("!#%*") + _r(5, string.digits)


GENERATORS = {
    "stripe_live": lambda: "sk" + "_live_" + _r(24),
    "openai": lambda: "sk-" + "proj-" + _r(48),
    "github": lambda: "gh" + "p_" + _r(36),
    "aws_key_id": lambda: "AK" + "IA" + _r(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
    "password": _password,
    "internal_email": lambda: "priya.sharma" + "@" + "acme-corp.internal",
    "qxid": lambda: "QX" + _r(5, string.ascii_uppercase + string.digits),
}


def _gen(spec: str) -> str:
    kind, _, arg = spec.partition(":")
    if kind in GENERATORS:
        return GENERATORS[kind]()
    if kind == "base62":
        return _r(int(arg))
    if kind == "base64":
        return _r(int(arg), ALNUM + "+/")
    raise SystemExit(f"unknown generator {spec}")


MARKER = ".zerotrace-demo"


def fixture_sets() -> list[str]:
    return sorted(p for p in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, p)))


def checked_set(name: str) -> str:
    """Only a directory that already exists under demo/fixtures, by exact name."""
    if name not in fixture_sets():
        raise SystemExit(f"unknown fixture set {name!r}; choose one of {fixture_sets()}")
    return os.path.join(FIXTURES, name)


def checked_dest(dest: str) -> str:
    """Absolute destination for the throwaway repo. Never a home, root or repo directory."""
    resolved = os.path.realpath(os.path.expanduser(dest))
    forbidden = {os.path.realpath(p) for p in (os.sep, os.path.expanduser("~"), os.getcwd(),
                                               os.path.dirname(FIXTURES))}
    if resolved in forbidden or len(resolved.split(os.sep)) < 3:
        raise SystemExit(f"refusing to use {resolved} as a demo directory")
    if os.path.isdir(os.path.join(resolved, ".git")) and not os.path.exists(
            os.path.join(resolved, MARKER)):
        raise SystemExit(f"{resolved} looks like a real repository; refusing to touch it")
    return resolved


def render(name: str, dest: str) -> None:
    src = checked_set(name)
    dest = checked_dest(dest)
    for root, _dirs, files in os.walk(src):
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), src)
            # dot.* files are named that way so they are not hidden in the repository; the
            # rendered demo repo needs them under their real names.
            target = os.path.join(dest, rel.replace("dot.env", ".env")
                                          .replace("dot.zerotrace.yml", ".zerotrace.yml"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(os.path.join(root, fname), encoding="utf-8") as f:
                text = f.read()
            text = re.sub(r"\{\{gen:([a-z_0-9:]+)\}\}", lambda m: _gen(m.group(1)), text)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    with open(os.path.join(dest, MARKER), "w", encoding="utf-8") as f:
        f.write("Throwaway repo rendered by demo/render_fixtures.py. Safe to delete.\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--fresh"]
    if len(args) != 2:
        raise SystemExit(__doc__)
    target = checked_dest(args[1])
    if "--fresh" in sys.argv and os.path.isdir(target):
        # Only ever delete a directory this script created.
        if not os.path.exists(os.path.join(target, MARKER)):
            raise SystemExit(f"{target} was not created by this script; refusing --fresh")
        shutil.rmtree(target, ignore_errors=True)
    render(args[0], target)

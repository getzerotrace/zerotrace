"""Catch `git commit --no-verify`: git skips the pre-commit hook for it, but not post-commit.

The pre-commit hook leaves an empty file named after the tree it scanned in
`<git-dir>/zerotrace/verified/`. The installed post-commit shim (installer.py) looks for HEAD's
tree there in shell, so an ordinary commit never starts Python for it; a commit without a
stamp skipped the scan, and `zerotrace post-commit` (cli.py) scans it and warns. A stamp is
empty, costs nothing when missing (the commit is just scanned once more) and expires.
"""
import contextlib
import os
import re
import time

from . import gitutil

_OID_RE = re.compile(r"[0-9a-f]{40,64}")
_MAX_AGE_SECONDS = 7 * 24 * 3600


def _folder() -> str:
    return os.path.join(gitutil.state_dir(), "verified")


def _rev(*args: str) -> str:
    """The object id a revision names, or "" when it names nothing."""
    out = gitutil.git("rev-parse", "-q", "--verify", *args, check=False).strip()
    return out if _OID_RE.fullmatch(out) else ""


def mark_verified() -> None:
    """Stamp the tree the index holds now. Never raises: no stamp costs one extra scan."""
    with contextlib.suppress(Exception):
        tree = gitutil.git("write-tree").strip()
        if _OID_RE.fullmatch(tree):
            folder = _folder()
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, tree), "w", encoding="utf-8"):
                pass                                    # the name is the whole stamp
            _expire(folder)


def _expire(folder: str) -> None:
    cutoff = time.time() - _MAX_AGE_SECONDS
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        with contextlib.suppress(OSError):
            if os.path.getmtime(path) < cutoff:
                os.remove(path)


def _rebasing() -> bool:
    git_dir = gitutil.git("rev-parse", "--git-dir", check=False).strip()
    return bool(git_dir) and any(os.path.isdir(os.path.join(git_dir, name))
                                 for name in ("rebase-merge", "rebase-apply"))


def commit_to_check() -> str | None:
    """HEAD, when it was made without the pre-commit scan and should be looked at now.

    None when its tree was scanned, when it is a merge (its parents were checked as they were
    made) and while a rebase replays commits that have already been through.
    """
    tree = _rev("HEAD^{tree}")
    if not tree or os.path.exists(os.path.join(_folder(), tree)):
        return None
    if _rev("HEAD^2") or _rebasing():
        return None
    return _rev("HEAD") or None


def undo_command() -> str:
    """The one command that takes HEAD back and leaves its changes staged."""
    return "git reset --soft HEAD~1" if _rev("HEAD~1") else "git update-ref -d HEAD"

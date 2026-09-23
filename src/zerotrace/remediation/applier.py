"""Apply an APPROVED fix to the INDEX (what will be committed), and mirror it to the work
tree only when the work-tree line is identical. Unstaged edits are never staged."""
import os
import re
import subprocess

from .. import gitutil

_ENV_SAFE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class StaleIndexError(RuntimeError):
    pass


def _split(data: str) -> list[str]:
    return data.splitlines(keepends=True)


def _replace_line(lines: list[str], line_no: int, expected: str, new_text: str) -> bool:
    if not (1 <= line_no <= len(lines)):
        return False
    old = lines[line_no - 1]
    if old.rstrip("\r\n") != expected:
        return False
    ending = old[len(old.rstrip("\r\n")):]
    lines[line_no - 1] = new_text.rstrip("\r\n") + ending
    return True


def _write_index_blob(path: str, content: str) -> None:
    entry = gitutil.git("ls-files", "-s", "--", path).split()
    mode = entry[0] if entry else "100644"
    sha = gitutil.git("hash-object", "-w", "--stdin",
                      input_bytes=content.encode("utf-8")).strip()
    gitutil.git("update-index", "--cacheinfo", f"{mode},{sha},{path}")


def apply(path: str, line_no: int, new_text: str, expected_old: str | None = None) -> str:
    """Replace one staged line. Returns 'index+worktree' or 'index'."""
    staged = gitutil.blob_text("", path)
    if staged is None:
        raise StaleIndexError(f"{path} is no longer staged")
    lines = _split(staged)
    expected = expected_old if expected_old is not None else \
        (lines[line_no - 1].rstrip("\r\n") if 1 <= line_no <= len(lines) else "")
    if not _replace_line(lines, line_no, expected, new_text):
        raise StaleIndexError(f"{path}:{line_no} changed since the scan; re-run zerotrace review")
    _write_index_blob(path, "".join(lines))

    full = gitutil.resolved_in_repo(path)
    try:
        with open(full, encoding="utf-8", newline="") as f:
            wt_lines = _split(f.read())
    except OSError:
        return "index"
    if _replace_line(wt_lines, line_no, expected, new_text):
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.writelines(wt_lines)
        return "index+worktree"
    return "index"


def unstage_and_ignore(path: str) -> list[str]:
    """Remove `path` from the commit, gitignore it, and (for .env) add a keys-only example.
    Returns the list of actions taken, for the audit log and the terminal."""
    actions: list[str] = []
    root = os.path.realpath(gitutil.repo_root())
    gitutil.resolved_in_repo(path)
    staged = gitutil.blob_text("", path)
    gitutil.git("rm", "--cached", "--quiet", "--", path)
    actions.append(f"unstaged {path} (the file stays on disk)")

    if not _is_ignored(path):
        gitignore = os.path.join(root, ".gitignore")
        existing = ""
        if os.path.exists(gitignore):
            with open(gitignore, encoding="utf-8") as f:
                existing = f.read()
        entry = "/" + path.replace(os.sep, "/")
        with open(gitignore, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"# added by zerotrace: never commit this file\n{entry}\n")
        gitutil.git("add", "--", ".gitignore")
        actions.append(f"added {entry} to .gitignore")

    base = os.path.basename(path)
    if base.startswith(".env") and staged is not None:
        example_rel = os.path.join(os.path.dirname(path), ".env.example")
        example = gitutil.resolved_in_repo(example_rel)
        if not os.path.exists(example):
            keys = [m.group(1) for m in map(_ENV_SAFE.match, staged.splitlines()) if m]
            with open(example, "w", encoding="utf-8") as f:
                f.write("# Copy to .env and fill in real values locally. Never commit .env.\n")
                f.writelines(f"{k}=\n" for k in keys)
            gitutil.git("add", "--", example_rel)
            actions.append(f"created {example_rel} with {len(keys)} keys and no values")
    return actions


def _is_ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", path],
                          capture_output=True).returncode == 0

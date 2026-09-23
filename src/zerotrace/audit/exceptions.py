"""Time-bound, reasoned exceptions keyed by finding fingerprint.

Two stores, read in this order:

1. `.zerotrace-exceptions.json` in the repo root, **committed and reviewed like code**. An
   exception silences a security control, so it belongs in a pull request where a second person
   sees the reason and the expiry, not in a file only its author can see.
2. `.git/zerotrace/exceptions.json`, private to the developer. This is where the interactive
   `[E]` flow writes, so nobody is forced to commit a file mid-commit; `zerotrace exceptions
   --promote` moves entries into the shared file when they are ready to be reviewed.

Both are keyed by fingerprint (rule + path + hash of the line), never by value, and both
expire. Newer entries also name the rule and the file, so a reviewer can tell what an entry
covers without reverse-engineering a hash.
"""
import json
import os
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .. import gitutil

SHARED_FILE = ".zerotrace-exceptions.json"
SHARED, LOCAL = "shared", "local"


@dataclass(frozen=True)
class Entry:
    """One exception, as `zerotrace exceptions` lists it."""
    scope: str                  # SHARED | LOCAL
    fingerprint: str
    reason: str
    created_at: str
    expires_at: str
    active: bool
    rule_id: str = ""           # empty for entries recorded before they were stored
    path: str = ""


def shared_path() -> str:
    return os.path.join(gitutil.repo_root(), SHARED_FILE)


def local_path() -> str:
    return os.path.join(gitutil.state_dir(), "exceptions.json")


def _path_for(scope: str) -> str:
    if scope == SHARED:
        return shared_path()
    if scope == LOCAL:
        return local_path()
    raise ValueError(f"unknown exception scope: {scope!r}")


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("exceptions", data) if isinstance(data, dict) else {}  # tolerate both shapes
    if not isinstance(entries, dict):
        return {}
    # The shared file arrives through pull requests, so its shape is not trusted: anything
    # that is not an object cannot be an exception.
    return {key: value for key, value in entries.items() if isinstance(value, dict)}


def _write(path: str, entries: dict, shared: bool) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload: dict = {"exceptions": entries}
    if shared:
        payload = {
            "note": "Reviewed exceptions. Each one silences a finding until it expires; "
                    "fingerprints only, never values. Changes belong in a pull request.",
            "exceptions": entries,
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def _entry(reason: str, ttl_days: int, rule_id: str, path: str) -> dict:
    now = datetime.now(UTC)
    entry = {
        "reason": reason,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
    }
    if rule_id:
        entry["rule_id"] = rule_id
    if path:
        entry["path"] = path
    return entry


def add(fingerprint: str, reason: str, ttl_days: int, shared: bool = False, *,
        rule_id: str = "", path: str = "") -> str:
    """Record an exception. Returns the file it was written to.

    `rule_id` and `path` say which finding the exception covers, for whoever reviews or
    browses it later. Matching never uses them (the fingerprint does that).
    """
    target = shared_path() if shared else local_path()
    entries = _read(target)
    entries[fingerprint] = _entry(reason, ttl_days, rule_id, path)
    _write(target, entries, shared)
    return target


def _active(entry: dict) -> bool:
    try:
        return datetime.now(UTC) < datetime.fromisoformat(entry["expires_at"])
    except (KeyError, TypeError, ValueError):
        return False        # an unparseable exception is not an exception


def is_active(fingerprint: str) -> bool:
    for path in (shared_path(), local_path()):
        entry = _read(path).get(fingerprint)
        if entry and _active(entry):
            return True
    return False


def listing() -> list[Entry]:
    """Every exception in both stores, the latest expiry first."""
    rows = [
        Entry(scope=scope, fingerprint=fingerprint,
              reason=str(entry.get("reason", "")),
              created_at=str(entry.get("created_at", "")),
              expires_at=str(entry.get("expires_at", "")),
              active=_active(entry),
              rule_id=str(entry.get("rule_id", "")),
              path=str(entry.get("path", "")))
        for scope in (SHARED, LOCAL)
        for fingerprint, entry in _read(_path_for(scope)).items()
    ]
    return sorted(rows, key=lambda row: row.expires_at, reverse=True)


def promote(fingerprints: Collection[str] | None = None) -> tuple[int, str]:
    """Move still-active local exceptions into the shared, reviewable file.

    All of them by default, or only those in `fingerprints`. Expired entries stay where they
    are: an out-of-date reason is not something to put in front of a reviewer.
    """
    local = _read(local_path())
    movable = {key: value for key, value in local.items()
               if _active(value) and (fingerprints is None or key in fingerprints)}
    if not movable:
        return 0, shared_path()
    shared = _read(shared_path())
    shared.update(movable)
    _write(shared_path(), shared, shared=True)
    _write(local_path(), {k: v for k, v in local.items() if k not in movable}, shared=False)
    return len(movable), shared_path()


def revoke(fingerprint: str, scope: str) -> bool:
    """Delete one exception, so the finding it covered blocks again. Returns whether it existed."""
    target = _path_for(scope)
    entries = _read(target)
    if entries.pop(fingerprint, None) is None:
        return False
    _write(target, entries, shared=scope == SHARED)
    return True


def prune() -> int:
    """Drop expired entries from both stores. Returns how many were removed."""
    removed = 0
    for scope in (SHARED, LOCAL):
        target = _path_for(scope)
        entries = _read(target)
        if not entries:
            continue
        keep = {key: value for key, value in entries.items() if _active(value)}
        removed += len(entries) - len(keep)
        if len(keep) != len(entries):
            _write(target, keep, shared=scope == SHARED)
    return removed

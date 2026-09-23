"""Append-only, tamper-evident, redacted audit log (hash-chained entries)."""
import hashlib
import json
import os

from .. import gitutil

_GENESIS_HASH = "0" * 64


def log_path() -> str:
    return os.path.join(gitutil.state_dir(), "audit.log.jsonl")


def record(event: dict, prev_hash: str) -> str:
    """Entry stores fingerprints + decisions only; prev_hash gives tamper-evidence."""
    payload = json.dumps({"event": event, "prev_hash": prev_hash}, sort_keys=True)
    new_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "prev_hash": prev_hash, "hash": new_hash}) + "\n")
    return new_hash


def _last_hash() -> str:
    path = log_path()
    if not os.path.exists(path):
        return _GENESIS_HASH
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    last_line = lines[-1] if lines else None
    if not last_line:
        return _GENESIS_HASH
    return json.loads(last_line)["hash"]


def append(event: dict) -> str:
    """Convenience wrapper: read the chain tip and record the next entry."""
    return record(event, _last_hash())

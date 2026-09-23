"""Detect -> post-process -> (optional, batched) classify -> decide. One path for every mode."""
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import replace

from .collectors.staged_diff import Changeset
from .detectors import SEVERITY_ORDER, Finding, downgrade
from .detectors import code_assign, composed, pii as pii_det, rulepack, secrets as secret_det
from .detectors import sensitive_files
from .detectors.filters import (
    is_digest, is_hash_context, is_lockfile, is_placeholder, is_uuid, looks_like_prose,
)
from .policy.engine import Decision, decide

_SOURCE_PRIORITY = {"sensitive_files": 5, "rulepack": 4, "composed": 4, "code_assign": 3,
                    "detect_secrets": 2, "pii": 1}
_ENTROPY_ONLY = {"Base64 High Entropy String", "Hex High Entropy String"}


def detect(changeset: Changeset, cfg) -> list[Finding]:
    findings: list[Finding] = []
    findings += sensitive_files.scan(changeset, cfg)
    findings += rulepack.scan(changeset.units, cfg)
    findings += code_assign.scan(changeset.units, cfg)
    findings += composed.scan(changeset.units, cfg)
    findings += secret_det.scan(changeset, cfg)
    findings += pii_det.scan(changeset.units, cfg)
    return postprocess(findings, cfg)


def _baseline_key(file_path: str, root: str) -> str:
    """A baseline filename as the scanner spells it: repo-relative, forward slashes.

    Baselines in the wild carry all three spellings - `src/a.py`, `./src/a.py`, and an
    absolute `/Users/someone/repo/src/a.py` (older `detect-secrets scan` runs and anything
    invoked with an absolute path write that one). Findings always carry the repo-relative
    path, so an unnormalised key simply never matched and the whole baseline silently
    suppressed nothing - the feature that lets an existing repository adopt ZeroTrace.
    """
    normalized = file_path.replace("\\", "/")
    if root:
        root_prefix = os.path.abspath(root).replace("\\", "/").rstrip("/") + "/"
        if normalized.startswith(root_prefix):
            normalized = normalized[len(root_prefix):]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _load_baseline(root: str) -> dict[str, set[str]]:
    """detect-secrets baseline: path -> set of sha1(secret). Honoured by every detector."""
    path = os.path.join(root, ".secrets.baseline")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    for file_path, entries in (data.get("results") or {}).items():
        for entry in entries:
            if entry.get("hashed_secret"):
                out[_baseline_key(file_path, root)].add(entry["hashed_secret"])
    return out


def _baseline_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def _is_noise(f: Finding) -> bool:
    """Deterministic false-positive filters that apply to every heuristic detector."""
    value = f.matched_value
    if not value or f.severity == "critical" or f.source == "sensitive_files":
        return False
    if is_placeholder(value) or is_uuid(value):
        return True
    if f.source not in ("detect_secrets", "code_assign"):
        return False
    if looks_like_prose(value):
        return True  # "A password is required." is a message, not a password
    if is_digest(value) and (is_hash_context(f.line_text) or is_hash_context(f.identifier)):
        return True  # "hashed_secret": "<sha1>" stores a hash, not the secret
    return f.rule_id in _ENTROPY_ONLY and (is_lockfile(f.path) or is_hash_context(f.line_text))


def _is_baselined(f: Finding, baseline: dict[str, set[str]]) -> bool:
    """Reviewed and accepted earlier, recorded as a hash in .secrets.baseline."""
    return bool(f.matched_value) and _baseline_hash(f.matched_value) in baseline.get(f.path, ())


def _context_adjusted(f: Finding) -> Finding:
    """Test and docs paths lower heuristic findings one level; provider formats stay."""
    heuristic = f.source in ("code_assign", "detect_secrets")
    if f.file_class in ("test", "docs") and heuristic and f.severity != "critical":
        return replace(f, severity=downgrade(f.severity))
    return f


def postprocess(findings: list[Finding], cfg) -> list[Finding]:
    baseline = _load_baseline(getattr(cfg, "repo_root", "") or os.getcwd())
    kept = [_context_adjusted(f) for f in findings
            if not _is_noise(f) and not _is_baselined(f, baseline)]
    return dedupe(kept)


# Detectors whose findings drive their own remediation (a synthetic value, a masked span, a
# blocked instruction) are kept per value. Collapsing them into the line's worst secret finding
# would silently drop an action: the gateway, for example, would forward an injected instruction
# to the model because a secret on the same line outranked it.
_PER_VALUE_SOURCES = ("pii", "prompt_injection", "confidentiality")


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One secret finding per (path, line); PII/injection/classification findings per value."""
    best: dict[tuple, Finding] = {}
    for f in findings:
        key: tuple = (f.path, f.line_no, f.source, f.matched_value) \
            if f.source in _PER_VALUE_SOURCES else (f.path, f.line_no, "secret")
        cur = best.get(key)
        rank = (SEVERITY_ORDER.get(f.severity, 0), _SOURCE_PRIORITY.get(f.source, 0))
        if cur is None or rank > (SEVERITY_ORDER.get(cur.severity, 0),
                                  _SOURCE_PRIORITY.get(cur.source, 0)):
            best[key] = f
    # File-level findings first (they can resolve the whole file), then by path/line.
    return sorted(best.values(), key=lambda f: (f.line_no != 0, f.path, f.line_no))


def decide_all(findings: list[Finding], cfg, use_model: bool = True) -> list[Decision]:
    verdicts: dict = {}
    if use_model and cfg.model_enabled:
        from .classifier import batch
        verdicts = batch.classify_medium(findings, cfg)
    decisions = []
    for i, f in enumerate(findings):
        if i in verdicts:
            decisions.append(decide(f, cfg, verdict=verdicts[i]))
        elif not use_model and f.severity in cfg.warn_severity:
            decisions.append(decide(f, replace(cfg, model_enabled=False)))
        else:
            decisions.append(decide(f, cfg))
    return decisions


def scan(changeset: Changeset, cfg, use_model: bool = True) -> list[Decision]:
    return decide_all(detect(changeset, cfg), cfg, use_model=use_model)

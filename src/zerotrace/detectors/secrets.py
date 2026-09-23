"""Wrap detect-secrets. Online verification disabled -> fully offline."""
import os
import re
import shutil
import tempfile
from collections import defaultdict

from detect_secrets.core import scan as ds_scan
from detect_secrets.settings import default_settings

from . import Finding
from .. import gitutil
from ..collectors.staged_diff import Changeset, Unit

# Entropy-only plugins can't tell a real secret from a realistic-looking
# placeholder -> medium confidence, eligible for the LLM tie-break.
# "Secret Keyword" fires on any `secret = "..."` regardless of the value, so it is graded
# medium as well; the code-assignment detector (entropy-aware) or the model settles it.
_MEDIUM_TYPES = {"Base64 High Entropy String", "Hex High Entropy String", "Secret Keyword"}
# Informational only; not a credential by itself.
_LOW_TYPES = {"Public IP (ipv4)"}
_CRITICAL_TYPES = {"Private Key"}

_EXPLAIN = {
    "Private Key": "Private key material. It impersonates its owner and must be rotated if pushed.",
    "Secret Keyword": "A credential-named variable is assigned a literal value.",
    "Basic Auth Credentials": "A URL embeds a username and password.",
    "JSON Web Token": "A signed JWT is a bearer credential until it expires.",
    "Base64 High Entropy String": "A random-looking string that may be key material.",
    "Hex High Entropy String": "A random-looking hex string that may be key material.",
}


def _severity_and_confidence(secret_type: str) -> tuple[str, float]:
    if secret_type in _CRITICAL_TYPES:
        return "critical", 0.95
    if secret_type in _MEDIUM_TYPES:
        return "medium", 0.55
    if secret_type in _LOW_TYPES:
        return "low", 0.3
    return "high", 0.9


def _slugify(secret_type: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", secret_type.lower()).strip("_")


def _scan_content(path: str, content: str):
    """Run detect-secrets over `content` as if it were `path`. Yields raw detect-secrets hits."""
    workdir = tempfile.mkdtemp(prefix="zerotrace-")
    try:
        with default_settings():
            # Keep the real basename so detect-secrets' filename filters (lockfiles,
            # swagger, etc.) still apply to the temp copy.
            tmp_path = os.path.join(workdir, os.path.basename(path) or "file.txt")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                tmp.write(content)
            try:
                yield from ds_scan.scan_file(tmp_path)
            finally:
                os.unlink(tmp_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _finding_from_secret(secret, path: str, line_no: int, file_class: str, line_text: str,
                         context_snippet: str) -> Finding:
    severity, confidence = _severity_and_confidence(secret.type)
    return Finding(
        rule_id=secret.type, kind=_slugify(secret.type),
        severity=severity, confidence=confidence, path=path,
        line_no=line_no, file_class=file_class,
        line_text=line_text, context_snippet=context_snippet,
        source="detect_secrets",
        explanation=_EXPLAIN.get(secret.type, f"{secret.type} detected."),
        matched_value=secret.secret_value or "",
    )


def scan(changeset: Changeset | list[Unit], cfg) -> list[Finding]:
    if isinstance(changeset, list):  # back-compat: a bare list of staged units
        changeset = Changeset(units=changeset, paths=sorted({u.path for u in changeset}))

    by_path: dict[str, list[Unit]] = defaultdict(list)
    for unit in changeset.units:
        if unit.file_class == "generated":
            continue
        by_path[unit.path].append(unit)

    findings: list[Finding] = []
    for path, path_units in by_path.items():
        added_lines = {u.line_no: u for u in path_units}
        content = gitutil.blob_text(changeset.rev, path)
        if content is None:
            continue
        for secret in _scan_content(path, content):
            if secret.line_number not in added_lines:
                continue  # only flag lines actually being added
            unit = added_lines[secret.line_number]
            findings.append(_finding_from_secret(secret, path, secret.line_number,
                                                  unit.file_class, unit.text, unit.window))
    return findings


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan arbitrary text not backed by a git blob (agent/tool/RAG payloads via the gateway).
    Every line is treated as "added" since there is no staged/committed baseline to diff against.
    """
    lines = text.splitlines()
    findings: list[Finding] = []
    for secret in _scan_content(path, text):
        if not (1 <= secret.line_number <= len(lines)):
            continue
        line_text = lines[secret.line_number - 1]
        findings.append(_finding_from_secret(secret, path, secret.line_number, "code",
                                             line_text, line_text))
    return findings

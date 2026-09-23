"""Redaction MUST run before a candidate reaches a prompt, a log, or disk.

The model sees the *shape* of a value (length, charset, entropy, skeleton) and the code
around it with every literal masked, never the value itself.
"""
import os
import re

from . import prompt
from ..detectors import entropy
from ..detectors.filters import is_env_reference

_STRING_LIT = re.compile(r"""(["'`])((?:\\.|(?!\1).)*)\1""")
_TOKENISH = re.compile(r"[A-Za-z0-9+/=_\-.~]{16,}")
_UNQUOTED_VALUE = re.compile(r"^(\s*(?:export\s+|ENV\s+|ARG\s+|-\s+)?[\w.\-]+\s*[=:]\s*)([^\s\"'`(){}\[\]#][^\s#]*)(\s*(?:#.*)?)$")
_SECRETISH = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]+PRIVATE KEY-----)")
REDACTED = "<REDACTED>"
_MARKER = re.compile(r"^<(CANDIDATE|REDACTED|STR len=\d+|TOKEN len=\d+|VAL len=\d+)>$")

_PUBLIC_PREFIXES = (
    "sk_live_", "sk_test_", "rk_live_", "rk_test_", "pk_live_", "pk_test_", "whsec_",
    "sk-proj-", "sk-ant-", "ghp_", "gho_", "github_pat_", "glpat-", "xoxb-", "xoxp-",
    "AKIA", "ASIA", "AIza", "eyJ", "hf_", "npm_", "dapi", "SG.", "hvs.", "dckr_pat_",
)
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".rs": "rust", ".tf": "terraform", ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".toml": "toml", ".ini": "ini", ".properties": "properties", ".sh": "shell",
    ".md": "markdown", ".ipynb": "notebook", ".xml": "xml", ".swift": "swift",
}


def redact(value: str, kind: str) -> str:
    """Return a typed, length-hinted token. Never returns the raw value."""
    return f"<{kind.upper()} len={len(value)}>"


def language_of(path: str) -> str:
    base = os.path.basename(path).lower()
    if base.startswith(".env"):
        return "dotenv"
    if base.startswith("dockerfile"):
        return "dockerfile"
    return _LANG_BY_EXT.get(os.path.splitext(base)[1], "text")


def _mask_literals(line: str) -> str:
    def repl(m: re.Match) -> str:
        q, body = m.group(1), m.group(2)
        if _MARKER.match(body) or len(body) <= 3:
            return m.group(0)
        return f"{q}<STR len={len(body)}>{q}"
    return _STRING_LIT.sub(repl, line)


def _is_identifier_like(tok: str) -> bool:
    """WEBHOOK_SIGNING_SECRET, payments.gateway, clientSecretValue: names, not key material."""
    if not re.fullmatch(r"[A-Za-z_.]+", tok):
        return False  # digits/symbols mixed in: treat as possible key material
    parts = [p for p in re.split(r"[_.]", tok) if p]
    if len(parts) > 1 or tok.isupper() or tok.islower():
        return True
    return entropy.dictionary_ratio(tok) >= 0.4


def _mask_tokens(line: str) -> str:
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if tok.startswith("<") or _is_identifier_like(tok):
            return tok
        if entropy.shannon(tok) >= 3.0:
            return f"<TOKEN len={len(tok)}>"
        return tok
    return _TOKENISH.sub(repl, line)


def _mask_unquoted(line: str) -> str:
    m = _UNQUOTED_VALUE.match(line)
    if not m or m.group(2).isdigit() or _MARKER.match(m.group(2)):
        return line
    return f"{m.group(1)}<VAL len={len(m.group(2))}>{m.group(3)}"


def scrub_window(window: str, candidate: str = "", extra_values: tuple[str, ...] = ()) -> str:
    """Mask the candidate, every known value, every literal and every high-entropy token."""
    text = window
    if candidate:
        text = text.replace(candidate, "<CANDIDATE>")
    for value in sorted({v for v in extra_values if v and len(v) >= 4}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    text = _SECRETISH.sub(REDACTED, text)

    from ..detectors import rulepack
    rules = rulepack.load_rules()
    out_lines = []
    for line in text.splitlines():
        for _rule, value, _span in list(rulepack.scan_line(line, rules)):
            line = line.replace(value, REDACTED)
        line = _mask_literals(line)
        line = _mask_unquoted(line)
        line = _mask_tokens(line)
        out_lines.append(line)
    text = "\n".join(out_lines)

    # Belt and braces: if anything sensitive survived, withhold the window entirely.
    for value in (candidate, *extra_values):
        if value and len(value) >= 4 and value in text:
            return "<WINDOW WITHHELD>"
    return text


def _identifier_for(finding) -> str:
    if getattr(finding, "identifier", ""):
        return finding.identifier
    from ..detectors.code_assign import candidates
    for ident, value in candidates(finding.line_text or "", finding.path):
        if value == finding.matched_value or finding.matched_value in value:
            return ident
    return ""


def features(finding) -> dict:
    """Everything the model is allowed to know about a finding. No raw value, ever."""
    value = finding.matched_value or ""
    prefix = next((p for p in _PUBLIC_PREFIXES if value.startswith(p)), "")
    return {
        "detector": finding.source or "unknown",
        "rule": finding.rule_id,
        "identifier": _identifier_for(finding) or prompt.NONE,
        "language": language_of(finding.path),
        "file_class": finding.file_class,
        "file_name": os.path.basename(finding.path),
        "known_public_prefix": prefix or prompt.NONE,
        "value_shape": entropy.shape(value[len(prefix):] if prefix else value),
        "env_lookup_nearby": is_env_reference(finding.context_snippet or ""),
    }

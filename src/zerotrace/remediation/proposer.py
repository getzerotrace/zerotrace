"""Build a human-readable fix PREVIEW. Never edits files here.

Modes:
  placeholder -> [R] obviously-fake value (synthetic PII, <NAME> placeholder)
  reference   -> [V] language-aware env / vault reference
  unstage     -> [U] remove a sensitive file from the commit + .gitignore it
"""
import os
import re
from dataclasses import dataclass

from ..detectors.code_assign import env_name_of

_SYNTHETIC_EMAIL = "user@example.test"
_SYNTHETIC_PHONE = "+1-555-0100"
_PII_SYNTHETIC = {
    "pii_email": _SYNTHETIC_EMAIL, "pii_email_internal": _SYNTHETIC_EMAIL,
    "pii_phone": _SYNTHETIC_PHONE, "qxid_internal_id": "QX_PLACEHOLDER",
    "pii_pan_india": "XXXXX0000X", "pii_aadhaar": "0000 0000 0000",
    "pii_payment_card": "4111 1111 1111 1111", "pii_iban": "XX00 TEST 0000 0000",
}
_ASSIGNMENT_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_.]*)\s*([:=])\s*(.*)$")

# ext -> env-reference expression that REPLACES the whole quoted literal
_CODE_REF = {
    ".py": 'os.environ["{n}"]',
    ".js": "process.env.{n}", ".mjs": "process.env.{n}", ".cjs": "process.env.{n}",
    ".jsx": "process.env.{n}", ".ts": "process.env.{n}", ".tsx": "process.env.{n}",
    ".go": 'os.Getenv("{n}")',
    ".java": 'System.getenv("{n}")', ".kt": 'System.getenv("{n}")', ".scala": 'sys.env("{n}")',
    ".cs": 'Environment.GetEnvironmentVariable("{n}")',
    ".rb": 'ENV["{n}"]', ".php": "getenv('{n}')",
    ".rs": 'std::env::var("{n}").expect("{n} not set")',
    ".swift": 'ProcessInfo.processInfo.environment["{n}"]',
}
_CODE_NOTES = {
    ".py": "Make sure `import os` is present.",
    ".go": 'Make sure "os" is imported.',
}


@dataclass(frozen=True)
class Proposal:
    mode: str               # placeholder | reference | unstage
    new_line: str | None    # replacement for the staged line (None for unstage)
    env_name: str = ""
    note: str = ""


def _env_name(finding) -> str:
    if finding.env_name:
        return finding.env_name
    if finding.identifier:
        return env_name_of(finding.identifier)
    m = _ASSIGNMENT_RE.match(finding.line_text or "")
    if m:
        return env_name_of(m.group(2))
    return finding.kind.upper()


def _quoted_span(line: str, value: str) -> tuple[int, int] | None:
    """Span of the quoted literal that contains exactly `value`, quotes included."""
    idx = line.find(value)
    while idx != -1:
        start, end = idx - 1, idx + len(value)
        if start >= 0 and end < len(line) and line[start] == line[end] and line[start] in "\"'`":
            prefix = start
            while prefix > 0 and line[prefix - 1] in "rRbBuUf@":
                prefix -= 1
            return prefix, end + 1
        idx = line.find(value, idx + 1)
    return None


def _ext(path: str) -> str:
    base = os.path.basename(path).lower()
    if base.startswith(".env"):
        return ".env"
    if base.startswith("dockerfile"):
        return "dockerfile"
    return os.path.splitext(base)[1]


def _reference_line(finding, name: str, cfg) -> tuple[str, str]:
    line, value, ext = finding.line_text or "", finding.matched_value, _ext(finding.path)
    vault = getattr(cfg, "vault_scheme", "") if cfg else ""

    if ext in _CODE_REF:
        span = _quoted_span(line, value)
        expr = _CODE_REF[ext].format(n=name)
        if span:
            return line[:span[0]] + expr + line[span[1]:], \
                f"Set {name} in your environment or secret manager. {_CODE_NOTES.get(ext, '')}".strip()
        # value embedded inside a bigger string: keep syntax valid with a placeholder
        return line.replace(value, f"<{name}>"), \
            f"The value sits inside a larger string. Build it from {expr} by hand."
    if ext in (".tf", ".hcl"):
        span = _quoted_span(line, value)
        var = name.lower()
        if span:
            return line[:span[0]] + f"var.{var}" + line[span[1]:], \
                f'Declare `variable "{var}" {{ sensitive = true }}` and pass it via TF_VAR_{var}.'
        return line.replace(value, f"${{var.{var}}}"), f"Declare a sensitive variable {var}."
    if ext == "dockerfile":
        m = re.match(r"^(\s*(?:ENV|ARG)\s+)([A-Za-z_]\w*)[= ]", line)
        if m:
            return f"{m.group(1)}{m.group(2)}", \
                "Pass it at runtime (docker run -e / compose env_file) or use a BuildKit secret mount."
    ref = vault.format(repo=os.path.basename(getattr(cfg, "repo_root", "") or "repo"), name=name) \
        if vault else f"${{{name}}}"
    return line.replace(value, ref), (
        f"Resolved from {vault.split(':')[0]} at deploy time." if vault
        else f"Provide {name} via the environment. Most config loaders expand ${{VAR}}.")


def _unstage_proposal(finding) -> Proposal:
    is_env = os.path.basename(finding.path).startswith(".env")
    return Proposal("unstage", None, note=(
        f"Unstage {finding.path}, add it to .gitignore"
        + (", and create a keys-only .env.example." if is_env else ".")))


def _pii_proposal(finding, mode: str) -> Proposal:
    line, value = finding.line_text or "", finding.matched_value
    if mode == "reference" and finding.file_class != "test":
        name = "CUSTOMER_EMAIL" if "email" in finding.kind else _env_name(finding)
        return Proposal("reference", line.replace(value, f"${{{name}}}"), name,
                        "Load real contact data at runtime; never ship it in code.")
    synthetic = _PII_SYNTHETIC[finding.kind]
    return Proposal("placeholder", line.replace(value, synthetic) if value else line,
                    note="Synthetic, RFC-reserved test data.")


def propose(decision, mode: str = "reference", cfg=None) -> Proposal:
    finding = decision.finding
    if finding.kind == "composed_secret":
        # The value never appears verbatim on the line, so an automatic rewrite would be a
        # guess. Say what to do instead.
        return Proposal("manual", None, _env_name(finding),
                        "Assembled from parts: rotate the credential, delete the pieces, and "
                        f"read {_env_name(finding)} from the environment at run time.")
    if finding.line_no == 0 or finding.kind == "sensitive_file":
        return _unstage_proposal(finding)
    if finding.kind in _PII_SYNTHETIC:
        return _pii_proposal(finding, mode)

    name = _env_name(finding)
    line, value = finding.line_text or "", finding.matched_value
    if mode == "placeholder":
        return Proposal("placeholder", line.replace(value, f"<{name}>") if value else line, name,
                        "The placeholder is obviously fake, so the scanner won't re-flag it.")
    new_line, note = _reference_line(finding, name, cfg)
    return Proposal("reference", new_line, name, note)

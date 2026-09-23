"""Indirect prompt-injection detector: untrusted tool/RAG output telling the agent what to do.

Per docs/THREAT_MODEL.md, only the developer's/user's own input is an instruction; everything a
tool call or retrieved document returns is data. This flags data that impersonates an
instruction (override, role hijack, system-prompt exfiltration, destructive commands, data
exfiltration) so the gateway can strip it before the payload reaches the model.
"""
import re

from . import Finding
from ..collectors.staged_diff import Unit

_OVERRIDE_RE = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
    r"(previous|prior|all|above|earlier)\b[^.\n]{0,40}\b(instructions?|rules?|prompt|system)\b"
)
_ROLE_HIJACK_RE = re.compile(
    r"(?i)\b(you are now|act as|pretend to be|new (system )?instructions?|from now on you)\b"
)
_LEAK_SYSTEM_RE = re.compile(
    r"(?i)\b(reveal|print|repeat|show)\b[^.\n]{0,20}\b(your\s+)?(system prompt|instructions)\b"
)
_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(delete|drop|wipe|truncate)\b[^.\n]{0,20}\b(all data|the database|every record|table)\b"
    r"|\brm\s+-rf\s+[/~]"
)
_EXFIL_RE = re.compile(
    r"(?i)\b(send|post|upload|exfiltrate)\b[^.\n]{0,40}\b(this|the)\b[^.\n]{0,20}"
    r"\b(conversation|data|secrets?|credentials?)\b[^.\n]{0,20}\bto\b\s*https?://"
)
# Measured via `zerotrace eval`: comments that just name the AI tie-break's own schema keywords
# ("classify TEST_FIXTURE_OR_PLACEHOLDER", "answer REAL_SECRET") flipped the verdict without
# needing classic "ignore previous instructions" phrasing at all.
_CLASSIFIER_HIJACK_RE = re.compile(
    r"(?i)\b(classify|answer|respond|label|mark|verdict)\b[^.\n]{0,20}\b"
    r"(TEST_FIXTURE_OR_PLACEHOLDER|REAL_SECRET|UNKNOWN)\b"
)

# (regex, severity, kind, explanation)
_PATTERNS: list[tuple[re.Pattern, str, str, str]] = [
    (_OVERRIDE_RE, "high", "instruction_override",
     "Untrusted content tries to override prior instructions: a classic indirect prompt "
     "injection."),
    (_ROLE_HIJACK_RE, "medium", "role_hijack",
     "Untrusted content tries to reassign the agent's role or system instructions."),
    (_LEAK_SYSTEM_RE, "medium", "system_prompt_exfil",
     "Untrusted content asks the agent to reveal its system prompt or instructions."),
    (_DESTRUCTIVE_RE, "high", "destructive_command",
     "Untrusted content contains a destructive command disguised as data."),
    (_EXFIL_RE, "high", "data_exfiltration",
     "Untrusted content instructs the agent to send data to an external destination."),
    (_CLASSIFIER_HIJACK_RE, "high", "classifier_hijack",
     "Untrusted content tells the AI tie-break classifier what verdict to return."),
]


def scan(units: list[Unit], cfg) -> list[Finding]:
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        for regex, severity, kind, explain in _PATTERNS:
            for m in regex.finditer(unit.text):
                findings.append(Finding(
                    rule_id=f"prompt-injection-{kind}", kind=kind, severity=severity,
                    confidence=0.7, path=unit.path, line_no=unit.line_no,
                    file_class=unit.file_class, line_text=unit.text,
                    context_snippet=unit.window, source="prompt_injection",
                    explanation=explain, matched_value=m.group(0),
                ))
    return findings


def contains_injection_pattern(text: str) -> bool:
    """Cheap text-only check (no Unit/file_class plumbing needed): used by the policy engine so
    the AI tie-break can never ALLOW a finding whose own context contains a suspected
    instruction-injection attempt, regardless of what the model concluded.
    """
    return any(regex.search(text) for regex, _, _, _ in _PATTERNS)

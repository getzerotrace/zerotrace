"""Shared detector output type. Detectors only find; they never decide policy."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    rule_id: str            # e.g. "AWS Access Key", "pii_email_internal"
    kind: str                # slug used by policy/remediation, e.g. "aws_access_key"
    severity: str             # critical | high | medium | low
    confidence: float
    path: str
    line_no: int             # 0 = the whole file (sensitive-file findings)
    file_class: str          # code | config | test | docs | generated
    line_text: str = ""      # the exact staged line, used to build a fix diff
    context_snippet: str = ""
    identifier: str = ""     # variable/key name the value is bound to, if known
    source: str = ""         # detector that produced it: rulepack | code_assign | ...
    explanation: str = ""    # human "why is this risky" text
    env_name: str = ""       # suggested environment variable name for remediation
    # In-memory only: never logged, printed, or sent anywhere unredacted.
    matched_value: str = field(default="", repr=False, compare=False)


SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def downgrade(severity: str) -> str:
    order = ["low", "medium", "high", "critical"]
    return order[max(0, order.index(severity) - 1)] if severity in order else severity

"""Classification-marker detector: don't let labelled-confidential text leak into an LLM prompt.

Complements sensitive_files.py (whole files) and rulepack.py (credential shapes): this looks
for the classification banners organisations stamp on documents, tickets and emails.
"""
import re

from . import Finding
from ..collectors.staged_diff import Unit

# (regex, severity, kind, explanation)
_MARKERS: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"\bTOP[ -]SECRET\b", re.I), "critical", "top_secret",
     "Marked TOP SECRET. This classification must never reach an external model."),
    (re.compile(r"\bTRADE[ -]SECRET\b", re.I), "high", "trade_secret",
     "Marked as a trade secret. Disclosure can be legally actionable."),
    (re.compile(r"\bCOMPANY[ -]CONFIDENTIAL\b", re.I), "high", "company_confidential",
     "Marked company confidential."),
    (re.compile(r"\bPROPRIETARY\b", re.I), "high", "proprietary",
     "Marked proprietary: not for disclosure outside the organisation."),
    (re.compile(r"\bCONFIDENTIAL\b", re.I), "high", "confidential",
     "Marked confidential."),
    (re.compile(r"\bNDA[ -]PROTECTED\b", re.I), "high", "nda_protected",
     "Covered by a non-disclosure agreement."),
    (re.compile(r"\bDO NOT DISTRIBUTE\b", re.I), "high", "do_not_distribute",
     "Explicitly marked not for distribution."),
    (re.compile(r"\bTLP:\s*RED\b", re.I), "high", "tlp_red",
     "Traffic Light Protocol RED: named recipients only, no further sharing."),
    (re.compile(r"\bTLP:\s*AMBER\b", re.I), "medium", "tlp_amber",
     "Traffic Light Protocol AMBER: limited disclosure, need-to-know only."),
    (re.compile(r"\bINTERNAL USE ONLY\b", re.I), "medium", "internal_use_only",
     "Marked internal use only."),
    (re.compile(r"\bINTERNAL ONLY\b", re.I), "medium", "internal_only",
     "Marked internal only."),
    (re.compile(r"\bNOT FOR DISTRIBUTION\b", re.I), "medium", "not_for_distribution",
     "Marked not for distribution."),
]


def scan(units: list[Unit], cfg) -> list[Finding]:
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        for regex, severity, kind, explain in _MARKERS:
            for m in regex.finditer(unit.text):
                findings.append(Finding(
                    rule_id=f"classification-marker-{kind}", kind=kind, severity=severity,
                    confidence=0.9, path=unit.path, line_no=unit.line_no,
                    file_class=unit.file_class, line_text=unit.text,
                    context_snippet=unit.window, source="confidentiality",
                    explanation=explain, matched_value=m.group(0),
                ))
    return findings

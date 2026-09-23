"""Runtime security gateway: sanitize AI-agent / MCP-tool / RAG payloads before an LLM sees them.

Same detect -> decide pipeline the git hook uses (rulepack, code_assign, detect-secrets, PII,
policy engine), pointed at arbitrary text instead of a staged diff, plus two detectors specific
to this interception point: classification markers (detectors/confidentiality.py) and indirect
prompt injection (detectors/prompt_injection.py). Findings are always masked out of the returned
text rather than blocking the call outright; `GatewayResult.decisions` carries the full record
for logging/audit so nothing is silently dropped.
"""
from dataclasses import dataclass, field
from typing import cast

from .payload import DEFAULT_PATH, units_from_text
from ..classifier.redact import redact
from ..config import Config, load_config
from ..detectors import Finding, code_assign, confidentiality, pii, prompt_injection, rulepack
from ..detectors import secrets as secret_det
from ..pipeline import decide_all, postprocess
from ..policy.engine import Decision

_VERDICT_RANK = {"allow": 0, "warn": 1, "block": 2}


@dataclass(frozen=True)
class GatewayResult:
    verdict: str                                    # allow | warn | block: the worst decision
    sanitized_text: str                              # safe to forward to the LLM
    decisions: list[Decision] = field(default_factory=list)


def detect(units: list, cfg: Config) -> list:
    findings = []
    findings += rulepack.scan(units, cfg)
    findings += code_assign.scan(units, cfg)
    findings += pii.scan(units, cfg)
    findings += confidentiality.scan(units, cfg)
    findings += prompt_injection.scan(units, cfg)
    if units:
        findings += secret_det.scan_text(units[0].path, "\n".join(u.text for u in units))
    return postprocess(findings, cfg)


def _mask(text: str, decisions: list[Decision]) -> str:
    lines = text.splitlines()
    by_line: dict[int, list[Decision]] = {}
    for d in decisions:
        if d.action != "allow":
            by_line.setdefault(cast(Finding, d.finding).line_no, []).append(d)
    for line_no, hits in by_line.items():
        if not (1 <= line_no <= len(lines)):
            continue
        line = lines[line_no - 1]
        for d in sorted(hits, key=lambda d: len(cast(Finding, d.finding).matched_value or ""), reverse=True):
            f = cast(Finding, d.finding)
            value = f.matched_value
            if not value or value not in line:
                continue
            if f.source == "prompt_injection":
                line = line.replace(value, "[BLOCKED: possible prompt injection]")
            elif f.source == "confidentiality":
                line = line.replace(value, "<REDACTED CLASSIFIED MARKER>")
            else:
                line = line.replace(value, redact(value, f.kind))
        lines[line_no - 1] = line
    return "\n".join(lines)


def sanitize(text: str, *, path: str = DEFAULT_PATH, cfg: Config | None = None,
            use_model: bool = False) -> GatewayResult:
    """Detect -> decide -> mask. Never raises, never returns a finding's raw value.

    `use_model` defaults to off: this is a runtime interception point (agent tool calls, RAG
    context), so it stays deterministic and fast unless the caller opts in.
    """
    cfg = cfg or load_config()
    units = units_from_text(text, path=path)
    findings = detect(units, cfg)
    decisions = decide_all(findings, cfg, use_model=use_model)
    sanitized = _mask(text, decisions)
    worst = max((d.action for d in decisions), key=lambda a: _VERDICT_RANK.get(a, 0),
               default="allow")
    return GatewayResult(verdict=worst, sanitized_text=sanitized, decisions=decisions)

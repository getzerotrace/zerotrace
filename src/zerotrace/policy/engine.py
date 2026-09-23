"""The ONLY place that decides block/warn/allow. Pure + deterministic given its inputs.

Asymmetric trust in the model (docs/ADR/0002):
  * HIGH/CRITICAL never reach the model, so the model can't unblock a real leak.
  * For MEDIUM, the model may lower friction (placeholder -> allow) or raise it
    (REAL_SECRET -> block). Any model failure leaves the finding at WARN.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from ..detectors import SEVERITY_ORDER, Finding

_NOT_PROVIDED = object()
_ACTION_RANK = {"block": 0, "warn": 1, "allow": 2}


@dataclass(frozen=True)
class Decision:
    action: str          # block | warn | allow
    severity: str
    reason: str
    finding: Finding
    model_verdict: object = None


def by_priority(decisions: Iterable[Decision]) -> list[Decision]:
    """The one order every listing uses: the summary table, the panels under it, the
    full-screen reviewer and `scan --format json`.

    What blocks comes before what only warns; within each, the most severe first (critical,
    high, medium, low); then file and line, so the same findings never shuffle between runs.
    """
    return sorted(decisions, key=lambda d: (
        _ACTION_RANK.get(d.action, len(_ACTION_RANK)),
        -SEVERITY_ORDER.get(d.finding.severity, -1),
        d.finding.path,
        d.finding.line_no,
    ))


def _where(finding) -> str:
    return f"{finding.path}:{finding.line_no}" if finding.line_no else finding.path


def _active_exception(finding) -> bool:
    from ..audit import exceptions as audit_exceptions
    from ..audit.fingerprint import of_finding
    return audit_exceptions.is_active(of_finding(finding))


def _ask_model(finding, cfg):
    try:
        # Lazy import: never touch the model unless a MEDIUM finding exists.
        from ..classifier import llm
        return llm.classify(finding, cfg)
    except Exception:
        return None  # any classifier error fails closed to WARN


def _from_verdict(finding, cfg, verdict) -> "Decision":
    """Asymmetric trust: a MEDIUM finding may be allowed or escalated, never more."""
    cls = getattr(verdict, "classification", None)
    confidence = float(getattr(verdict, "confidence", 0.0))
    why = getattr(verdict, "reason", "")
    if cls == "TEST_FIXTURE_OR_PLACEHOLDER" and confidence >= cfg.model_allow_threshold:
        from ..detectors.prompt_injection import contains_injection_pattern
        context = finding.context_snippet or finding.line_text
        if contains_injection_pattern(context):
            # Measured via `zerotrace eval`: a nearby comment telling the classifier what to
            # answer can flip its verdict. Never let that verdict alone allow a finding through.
            return Decision(
                "warn", finding.severity,
                f"{finding.rule_id}: AI tie-break said placeholder, but the surrounding text "
                f"contains a suspected instruction-injection attempt, so the allow is not "
                f"honored: {why}", finding, verdict)
        return Decision("allow", finding.severity,
                        f"AI tie-break: placeholder/test fixture ({confidence:.2f}): {why}",
                        finding, verdict)
    if cls == "REAL_SECRET" and cfg.model_can_escalate \
            and confidence >= cfg.model_escalate_threshold:
        return Decision("block", finding.severity,
                        f"AI tie-break escalated to BLOCK: likely real secret "
                        f"({confidence:.2f}): {why}", finding, verdict)
    return Decision("warn", finding.severity,
                    f"{finding.rule_id}: AI tie-break says {cls} ({confidence:.2f}): {why}",
                    finding, verdict)


def _decide_medium(finding, cfg, verdict) -> "Decision":
    if cfg.model_enabled:
        if verdict is _NOT_PROVIDED:
            verdict = _ask_model(finding, cfg)
        if verdict is not None:
            return _from_verdict(finding, cfg, verdict)
    return Decision(
        "warn", finding.severity,
        f"{finding.rule_id} is ambiguous and could not be auto-classified; manual review required.",
        finding,
    )


def decide(finding, cfg, verdict=_NOT_PROVIDED) -> "Decision":
    severity = finding.severity

    if _active_exception(finding):
        return Decision(
            "allow", severity,
            f"{finding.rule_id} is covered by a previously approved, time-bound exception.",
            finding,
        )

    # HIGH/CRITICAL -> block. The model is never consulted here.
    if severity in cfg.block_severity:
        return Decision(
            "block", severity,
            f"{finding.rule_id} matched with high confidence in {_where(finding)}.",
            finding,
        )

    if severity in cfg.warn_severity:
        return _decide_medium(finding, cfg, verdict)

    # LOW confidence -> allow, but recorded in the audit log.
    return Decision(
        "allow", severity,
        f"{finding.rule_id} looks like a placeholder/public example (low confidence).",
        finding,
    )

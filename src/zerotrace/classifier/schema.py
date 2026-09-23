"""Validate model output. Anything invalid -> discard verdict (fail closed)."""
import json
import re
from dataclasses import dataclass

ALLOWED = {"REAL_SECRET", "PII", "TEST_FIXTURE_OR_PLACEHOLDER", "UNKNOWN"}
_MAX_REASON_WORDS = 20

# Passed to the runtime for constrained decoding (Ollama `format`, OpenAI `response_format`).
# parse() below still validates everything: the schema is a hint, not a trust boundary.
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": sorted(ALLOWED)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 160},
    },
    "required": ["classification", "confidence", "reason"],
    "additionalProperties": False,
}
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
# Small local models sometimes answer with a word instead of the requested
# 0-1 number; this is a courtesy fallback, not a relaxation of the range check.
_WORD_CONFIDENCE = {"low": 0.3, "medium": 0.6, "high": 0.9}


@dataclass(frozen=True)
class Verdict:
    classification: str
    confidence: float
    reason: str


def _extract_object(raw: str) -> dict | None:
    """Model output -> dict, tolerating code fences and surrounding prose."""
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    for candidate in (text, None):
        if candidate is None:
            match = _OBJECT_RE.search(text)
            if not match:
                return None
            candidate = match.group(0)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return data if isinstance(data, dict) else None
    return None


def _confidence_of(raw_confidence) -> float | None:
    if isinstance(raw_confidence, str) and raw_confidence.strip().lower() in _WORD_CONFIDENCE:
        return _WORD_CONFIDENCE[raw_confidence.strip().lower()]
    try:
        confidence = float(raw_confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return confidence if 0.0 <= confidence <= 1.0 else None


def parse(raw: str) -> "Verdict | None":
    data = _extract_object(raw)
    if data is None or data.get("classification") not in ALLOWED:
        return None
    confidence = _confidence_of(data.get("confidence"))
    if confidence is None:
        return None
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return Verdict(classification=data["classification"], confidence=confidence,
                   reason=" ".join(reason.split()[:_MAX_REASON_WORDS]))

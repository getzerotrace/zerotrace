"""Secrets assembled from parts: `key = "sk_live_" + tail`, or two halves joined later.

The documented bypass (docs/THREAT_MODEL.md) was that every other detector matches within a
single value, so splitting a credential across literals hid it completely. This resolves a
*small, local* amount of data flow instead: string literals bound to names earlier in the same
staged file, then joined with `+` on one line.

Deliberately narrow, because the cost of a false positive here is a blocked commit on ordinary
string building:
  * only `+` concatenation of literals and names we already resolved,
  * only when the joined value matches a provider rule, or lands on a credential-ish name and
    looks like key material,
  * URLs, paths and prose are rejected by the same filters the other detectors use.
Anything cleverer (slicing, `join()`, arithmetic, cross-file) is still out of scope and stays
in the threat model as a known gap.
"""
import re

from . import Finding, code_assign, rulepack
from .entropy import shannon
from .filters import is_placeholder
from ..collectors.staged_diff import Unit

_MAX_PARTS = 8
_MIN_JOINED = 12

# `target = a + "b" + c`  (assignment whose right-hand side is a + chain with 2+ operands)
_CONCAT_RE = re.compile(r"""
    (?P<target>[A-Za-z_$@][\w$.\-]*)
    ['"]?\]?\s*
    (?::\s*[\w\[\]<>,.|&'?\s]*?\s*(?==))?
    (?::=|=)\s*
    (?P<expr>(?:[^=;#\n]|=(?!=))+)
""", re.X)
_OPERAND_RE = re.compile(r"""
    (?P<quote>["'`])(?P<literal>(?:\\.|(?!(?P=quote)).)*)(?P=quote)
    |(?P<name>[A-Za-z_$@][\w$.]*)
""", re.X)
_EXPLAIN = ("A credential assembled from parts. Splitting a key across literals hides it from "
            "pattern matching, but the assembled value is still a live credential at run time.")


def _literals_by_name(units: list[Unit]) -> dict[str, dict[str, str]]:
    """path -> {identifier: literal}, for every simple string assignment in the staged lines."""
    table: dict[str, dict[str, str]] = {}
    for unit in units:
        for ident, value in code_assign.candidates(unit.text, unit.path):
            if value and len(value) <= 256:
                table.setdefault(unit.path, {}).setdefault(ident, value)
    return table


def _join_expression(expr: str, known: dict[str, str]) -> str | None:
    """Resolve `"a" + b + "c"` to its value, or None if any operand is unknown."""
    if "+" not in expr:
        return None
    parts = [p.strip() for p in expr.split("+")]
    if not (2 <= len(parts) <= _MAX_PARTS):
        return None

    joined = []
    for part in parts:
        match = _OPERAND_RE.fullmatch(part)
        if match is None:
            return None                      # a call, an index, arithmetic: out of scope
        if match.group("literal") is not None:
            joined.append(match.group("literal"))
            continue
        value = known.get(match.group("name"))
        if value is None:
            return None                      # unknown name: never guess
        joined.append(value)
    return "".join(joined)


def _classify(target: str, joined: str, cfg) -> tuple[str, float, str] | None:
    """(severity, confidence, rule_id) for an assembled value, or None to ignore it."""
    rules = rulepack.load_rules(tuple(getattr(cfg, "rules_extra", ()) or ()),
                               getattr(cfg, "repo_root", ""))
    for rule, _value, _span in rulepack.scan_line(joined, rules):
        return rule.severity, 0.95, f"composed-{rule.id}"

    vocab = code_assign._vocab(code_assign.words_of(target))
    if vocab is None or vocab[0] != "strong":
        return None
    if code_assign._value_is_benign(joined) or shannon(joined) < 3.2:
        return None
    return "high", 0.8, "composed-secret"


def scan(units: list[Unit], cfg) -> list[Finding]:
    known = _literals_by_name(units)
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        match = _CONCAT_RE.match(unit.text.strip())
        if match is None:
            continue
        joined = _join_expression(match.group("expr"), known.get(unit.path, {}))
        if joined is None or len(joined) < _MIN_JOINED or is_placeholder(joined):
            continue
        verdict = _classify(match.group("target"), joined, cfg)
        if verdict is None:
            continue
        severity, confidence, rule_id = verdict
        findings.append(Finding(
            rule_id=rule_id, kind="composed_secret", severity=severity, confidence=confidence,
            path=unit.path, line_no=unit.line_no, file_class=unit.file_class,
            line_text=unit.text, context_snippet=unit.window,
            identifier=match.group("target"), source="composed", explanation=_EXPLAIN,
            env_name=code_assign.env_name_of(match.group("target")), matched_value=joined,
        ))
    return findings

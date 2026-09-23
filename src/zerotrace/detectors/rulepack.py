"""Provider-format rule pack (YAML). Keyword prefilter -> regex -> placeholder/entropy checks."""
import functools
import os
import re
import sys
from dataclasses import dataclass
from importlib import resources

import yaml

from . import Finding
from .entropy import shannon
from .filters import is_placeholder
from ..collectors.staged_diff import Unit


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    keywords: tuple[str, ...]
    regex: re.Pattern
    secret_group: int = 0
    check_group: int | None = None
    min_entropy: float = 0.0
    env_name: str = ""
    explain: str = ""


def _parse(raw: dict, origin: str) -> Rule | None:
    try:
        return Rule(
            id=str(raw["id"]),
            title=str(raw.get("title", raw["id"])),
            severity=str(raw.get("severity", "high")),
            keywords=tuple(str(k).lower() for k in raw.get("keywords") or ()),
            regex=re.compile(raw["regex"]),
            secret_group=int(raw.get("secret_group", 0)),
            check_group=int(raw["check_group"]) if "check_group" in raw else None,
            min_entropy=float(raw.get("min_entropy", 0.0)),
            env_name=str(raw.get("env_name", "")),
            explain=str(raw.get("explain", "")).strip(),
        )
    except (KeyError, re.error, TypeError, ValueError) as exc:
        print(f"zerotrace: warning: skipping invalid rule in {origin}: {exc}", file=sys.stderr)
        return None


def _load_pack(text: str, origin: str) -> list[Rule]:
    data = yaml.safe_load(text) or {}
    rules = [_parse(r, origin) for r in data.get("rules", [])]
    return [r for r in rules if r is not None]


@functools.lru_cache(maxsize=8)
def load_rules(extra: tuple[str, ...] = (), repo_root: str = "") -> tuple[Rule, ...]:
    text = resources.files("zerotrace.detectors").joinpath("rules/default.yml").read_text("utf-8")
    rules = _load_pack(text, "default.yml")
    for path in extra:
        full = path if os.path.isabs(path) else os.path.join(repo_root, path)
        try:
            with open(full, encoding="utf-8") as f:
                rules.extend(_load_pack(f.read(), full))
        except OSError as exc:
            print(f"zerotrace: warning: rule pack {full} unreadable: {exc}", file=sys.stderr)
    return tuple(rules)


def _accepted(rule: Rule, match: re.Match) -> str | None:
    """The value to report for this match, or None if it fails the rule's own checks."""
    value = match.group(rule.secret_group) or ""
    checked = match.group(rule.check_group) if rule.check_group is not None else value
    if not value or not checked or is_placeholder(checked):
        return None
    if rule.min_entropy and shannon(checked) < rule.min_entropy:
        return None
    return value


def scan_line(text: str, rules: tuple[Rule, ...]):
    """Yield (rule, secret_value, span) for every rule hit on one line."""
    lower = text.lower()
    for rule in rules:
        if rule.keywords and not any(k in lower for k in rule.keywords):
            continue
        for match in rule.regex.finditer(text):
            value = _accepted(rule, match)
            if value is not None:
                yield rule, value, match.span(rule.secret_group)


def scan(units: list[Unit], cfg) -> list[Finding]:
    rules = load_rules(tuple(getattr(cfg, "rules_extra", ()) or ()), getattr(cfg, "repo_root", ""))
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        for rule, value, _span in scan_line(unit.text, rules):
            findings.append(Finding(
                rule_id=rule.id, kind=rule.id.replace("-", "_"), severity=rule.severity,
                confidence=0.95 if rule.severity == "critical" else 0.9,
                path=unit.path, line_no=unit.line_no, file_class=unit.file_class,
                line_text=unit.text, context_snippet=unit.window, source="rulepack",
                explanation=f"{rule.title}: {rule.explain}", env_name=rule.env_name,
                matched_value=value,
            ))
    return findings

"""Findings are listed in one order everywhere: what blocks before what warns, most severe
first (critical, high, medium, low), then by file and line."""
import io
import re

from rich.console import Console

from zerotrace import cli
from zerotrace.config import Config
from zerotrace.detectors import Finding
from zerotrace.policy.engine import Decision, by_priority
from zerotrace.ui import terminal


def decision(rule: str, severity: str, action: str, path: str = "app.py", line: int = 1) -> Decision:
    finding = Finding(rule_id=rule, kind="hardcoded_api_key", severity=severity, confidence=0.9,
                      path=path, line_no=line, file_class="code", line_text="x = 1",
                      source="rulepack", explanation="why it matters")
    return Decision(action, severity, "matched", finding)


# Deliberately scrambled: the order a detector run might produce.
MIXED = [decision("low-block", "low", "block"), decision("medium-warn", "medium", "warn"),
         decision("critical-block", "critical", "block"), decision("high-block", "high", "block"),
         decision("low-warn", "low", "warn"), decision("medium-block", "medium", "block")]
EXPECTED = ["critical-block", "high-block", "medium-block", "low-block", "medium-warn", "low-warn"]


def rules(decisions) -> list[str]:
    return [d.finding.rule_id for d in decisions]


def test_blocking_findings_come_first_most_severe_first_then_the_warnings():
    assert rules(by_priority(MIXED)) == EXPECTED


def test_ties_are_broken_by_file_and_line_so_the_order_never_shuffles():
    late = decision("r", "high", "block", "b.py", 9)
    middle = decision("r", "high", "block", "a.py", 20)
    early = decision("r", "high", "block", "a.py", 3)
    assert by_priority([late, middle, early]) == [early, middle, late]


def _first_positions(text: str) -> list[str]:
    found = {rule: text.index(rule) for rule in EXPECTED}
    return sorted(found, key=found.get)


def test_the_table_and_the_panels_under_it_use_the_same_order(monkeypatch):
    console = Console(file=io.StringIO(), force_terminal=False, width=200)
    monkeypatch.setattr(terminal, "console", console)
    terminal.headless_report(list(reversed(MIXED)), Config())
    output = console.file.getvalue()
    table, panels = re.split(r"the commit is held until", output, maxsplit=1)
    assert _first_positions(table) == EXPECTED, "summary table"
    assert _first_positions(panels) == EXPECTED, "the panels under it"


def test_json_output_follows_the_same_order():
    assert [row["rule"] for row in cli._decisions_to_json(list(reversed(MIXED)))] == EXPECTED

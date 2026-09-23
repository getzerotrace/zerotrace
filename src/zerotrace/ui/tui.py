"""The full-screen review app (`zerotrace review`), built with Textual.

Why this is NOT what the git hook runs: Textual takes over the whole screen and costs a
noticeable import on every start. A pre-commit hook must be fast, must work when git gives it
no terminal, and must not repaint a developer's scrollback. So the hook keeps the inline
rich output, and this richer experience is what `zerotrace review` opens when a terminal is
available. Both drive the same detectors, policy engine and remediation code.

Keys are accepted in either case: `v` and `V` do the same thing, because the menu shows the
capital letter.
"""
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from ..audit import exceptions as audit_exceptions
from ..audit import log as audit_log
from ..audit.fingerprint import of_finding
from ..classifier.redact import redact
from ..policy.engine import Decision, by_priority
from ..remediation import applier, proposer
from .terminal import decided_by
from .tui_common import (
    NAVIGATION, ButtonRow, ConfirmScreen, ListDetailApp, cell, esc, key, run_app, styled,
)

_SEVERITY_COLOUR = {"critical": "bold white on red", "high": "bold red",
                    "medium": "yellow", "low": "dim"}
_ACTION_COLOUR = {"block": "bold red", "warn": "yellow", "allow": "green"}
_STATE_MARK = {"open": "  ", "fixed": "[green]✓[/]", "excepted": "[yellow]≈[/]"}


def _short_where(finding) -> str:
    """basename:line. The directory is usually the same for every row and eats the column."""
    name = finding.path.rsplit("/", 1)[-1]
    return f"{name}:{finding.line_no}" if finding.line_no else f"{name} (file)"


def _verdict(decision) -> str:
    """BLOCK or WARN, coloured by severity: the two facts are read as one."""
    style = _SEVERITY_COLOUR.get(decision.finding.severity) or \
        _ACTION_COLOUR.get(decision.action, "")
    return styled(decision.action.upper(), style)


@dataclass
class Row:
    """One finding plus what has happened to it in this session."""
    decision: Decision
    state: str = "open"          # open | fixed | excepted
    note: str = ""


class ReasonScreen(ModalScreen[str]):
    """An exception silences a security control, so it must carry a reason."""

    CSS = """
    ReasonScreen { align: center middle; }
    #box { width: 70; max-width: 90%; height: auto; border: round $warning; padding: 1 2;
           background: $surface; }
    #box Label { width: 100%; }
    """
    # The reason box keeps ← and → for editing text, so ↓ and ↑ move between it and the
    # buttons (Tab works too, and so does a click).
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "focus_buttons", show=False),
        Binding("up", "focus_reason", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label("Why is this finding acceptable?")
            yield Label("[dim]It expires, and the value itself is never stored.[/]")
            yield Input(placeholder="e.g. vendor sample key, rotated 2026-09-01", id="reason")
            with ButtonRow():
                yield Button("Record exception", variant="warning", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#reason", Input).focus()

    def action_focus_buttons(self) -> None:
        self.query_one("#ok", Button).focus()

    def action_focus_reason(self) -> None:
        self.query_one("#reason", Input).focus()

    @on(Button.Pressed, "#ok")
    @on(Input.Submitted, "#reason")
    def _accept(self) -> None:
        reason = self.query_one("#reason", Input).value.strip()
        if reason:
            self.dismiss(reason)
        else:                           # say why nothing happened, and put the cursor back
            self.notify("An exception needs a reason.", severity="warning", timeout=4)
            self.action_focus_reason()

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss("")


_HELP = """\
## Resolving a finding

| Key | What it does |
| --- | --- |
| `V` | Replace the value with an environment or vault reference, in this file's language |
| `R` | Replace it with a safe placeholder (for examples and fixtures) |
| `U` | Unstage the whole file and add it to `.gitignore` |
| `E` | Record a time-bound exception — a written reason is required |
| `F` | Apply the `V` fix to every remaining finding that has one |

## Moving around

| Key | What it does |
| --- | --- |
| `J` / `K`, arrows | Next / previous finding (the mouse works too) |
| `O` | Show only the findings that are still open |
| `A` or `Q` | Leave. Anything still open keeps the commit blocked |

Upper and lower case both work everywhere. Every fix is written to the staged copy
only: your unstaged edits are left alone. Exceptions you record here are local to you until
`zerotrace exceptions -i` promotes them into the reviewed file.
"""


class ReviewApp(ListDetailApp[Row]):
    """Browse the staged findings and resolve them one at a time."""

    TITLE = "ZeroTrace review"
    TABLE_ID = "findings"
    # Four columns, not six: the list pane is under half the window, and a clipped severity
    # is worse than no severity. The full path and the "decided by" line live in the detail
    # pane, where there is room for them. The verdict is last, so it gets a fixed width.
    COLUMNS = ((" ", 1), ("Where", 16), ("Rule", 17), ("Verdict", 7))
    KEYS_HELP = _HELP
    ACTIONS = (
        ("fix_reference", "V  env/vault reference"),
        ("fix_placeholder", "R  safe placeholder"),
        ("unstage", "U  unstage the file + .gitignore"),
        ("exception", "E  exception, with a reason"),
    )
    BINDINGS = [
        key("v", "fix_reference", "env/vault ref"),
        key("r", "fix_placeholder", "placeholder"),
        key("u", "unstage", "unstage file"),
        key("e", "exception", "exception"),
        key("f", "fix_all", "fix all"),
        key("o", "toggle_resolved", "only open"),
        key("a", "leave", "abort", also="q,Q,escape"),
        *NAVIGATION,
    ]

    def __init__(self, decisions: list, cfg) -> None:
        super().__init__()
        # Same order as the inline table and panels: blocking before warning, most severe first.
        self.rows = [Row(decision) for decision in by_priority(decisions)]
        self.cfg = cfg
        self.hide_resolved = False

    def loaded(self) -> None:
        self.sub_title = f"{len(self.rows)} finding(s) holding this commit"
        self.redraw()

    # --- what the frame shows -------------------------------------------------------------
    def shown_items(self) -> list[Row]:
        return [row for row in self.rows if not self.hide_resolved or row.state == "open"]

    def cells(self, item: Row) -> tuple[str, ...]:
        finding = item.decision.finding
        return (_STATE_MARK[item.state], cell(_short_where(finding), 16),
                cell(finding.rule_id, 17), _verdict(item.decision))

    def actions_for(self, item: Row | None) -> set[str]:
        """The same choices the inline menu offers for this finding, nothing it cannot do."""
        if item is None or item.state != "open":
            return set()
        mode = proposer.propose(item.decision, "reference", self.cfg).mode
        if mode == "manual":
            return {"exception"}
        if mode == "unstage" or not item.decision.finding.line_no:
            return {"unstage", "exception"}
        return {"fix_reference", "fix_placeholder", "unstage", "exception"}

    def _masked(self, text: str) -> str:
        """Never show a raw value, not even in the pane the developer is reading."""
        for row in self.rows:
            other = row.decision.finding
            if other.matched_value:
                text = text.replace(other.matched_value, redact(other.matched_value, other.kind))
        return text

    def _shown(self, text: str | None) -> str:
        """Staged text as it may appear on screen: values masked, then escaped."""
        return esc(self._masked(text or ""))

    def describe(self, item: Row | None) -> str:
        if item is None:
            return "Nothing to review."
        finding = item.decision.finding
        where = f":{finding.line_no}" if finding.line_no else " (whole file)"
        lines = [
            f"[bold]{esc(finding.rule_id)}[/] "
            f"({styled(finding.severity, _SEVERITY_COLOUR.get(finding.severity, ''))})",
            f"[dim]{esc(finding.path + where)} · decided by {esc(decided_by(item.decision))}[/]",
            "",
            esc(finding.explanation or ""),
            "",
            f"[dim]{esc(item.decision.reason)}[/]",
        ]
        if finding.line_text:
            lines += ["", "[bold]Staged line[/]", f"  {self._shown(finding.line_text)}"]
        lines += self._fix_lines(item)
        if item.note:
            lines += ["", f"[green]{esc(item.note)}[/]"]
        return "\n".join(lines)

    def _fix_lines(self, item: Row) -> list[str]:
        reference = proposer.propose(item.decision, "reference", self.cfg)
        if reference.mode == "unstage":
            return ["", "[bold]Fix[/]", f"  [green]{esc(reference.note)}[/]", "",
                    "[dim]Press U to unstage and gitignore it.[/]"]
        if reference.mode == "manual":
            return ["", "[bold]Fix by hand[/]", f"  [yellow]{esc(reference.note)}[/]", "",
                    "[dim]No safe automatic rewrite: E records an exception, A aborts.[/]"]
        placeholder = proposer.propose(item.decision, "placeholder", self.cfg)
        return [
            "", "[bold]Proposed fix[/]",
            f"  [red]- {self._shown(item.decision.finding.line_text)}[/]",
            f"  [green]+ {self._shown(reference.new_line)}[/]",
            f"  [dim]{esc(reference.note)}[/]",
            "", f"[dim]or R:  {self._shown(placeholder.new_line)}[/]",
        ]

    def summary(self) -> str:
        open_rows = sum(1 for row in self.rows if row.state == "open")
        if not open_rows:
            return "[green]all findings resolved — press Q to finish the commit[/]"
        filtered = " · showing open only" if self.hide_resolved else ""
        return (f"[green]{len(self.rows) - open_rows} resolved[/] · [bold]{open_rows} open[/] "
                f"of {len(self.rows)}{filtered} · [dim]? for keys[/]")

    def outcome(self) -> int:
        """0 only when nothing is left open: the commit proceeds on a clean review."""
        return 0 if all(row.state != "open" for row in self.rows) else 1

    def _advance(self) -> None:
        """After a fix, land on the next finding that still needs a decision.

        Re-reading the list to find where you were is the tedious part of a long review, so
        the cursor does it: forward first, then wrapping round to any earlier open finding.
        """
        table = self.table
        start, count = table.cursor_row, len(self.listed)
        for step in range(1, count + 1):
            index = (start + step) % count
            if self.listed[index].state == "open":
                table.move_cursor(row=index)
                break
        self.show_detail()

    # --- actions -----------------------------------------------------------------------
    def _resolve(self, row: Row, state: str, note: str, method: str,
                 advance: bool = True) -> None:
        row.state, row.note = state, note
        audit_log.append({"fingerprint": of_finding(row.decision.finding),
                          "path": row.decision.finding.path,
                          "action": "remediated" if state == "fixed" else "exception",
                          "method": method})
        self.redraw(keep=row)
        if advance:
            self._advance()
        if all(r.state != "open" for r in self.rows):
            self.notify("Every finding is resolved. Press Q to finish.", timeout=6)

    def _apply_to(self, row: Row, mode: str, quiet: bool = False) -> bool:
        """Rewrite one finding. Returns whether it was resolved."""
        finding = row.decision.finding
        proposal = proposer.propose(row.decision, mode, self.cfg)
        if proposal.mode in ("unstage", "manual"):
            if not quiet:
                self.notify("This finding has no automatic rewrite; use U or E.",
                            severity="warning")
            return False
        try:
            where = applier.apply(finding.path, finding.line_no, proposal.new_line or "",
                                  finding.line_text)
        except applier.StaleIndexError as exc:
            if not quiet:
                self.notify(str(exc), severity="error", timeout=8, markup=False)
            return False
        self._resolve(row, "fixed", f"Applied to the {where.replace('+', ' and ')}.",
                      "vault_reference" if mode == "reference" else "placeholder",
                      advance=not quiet)
        return True

    def _open_current(self) -> Row | None:
        row = self.current()
        return row if row is not None and row.state == "open" else None

    def action_fix_reference(self) -> None:
        if row := self._open_current():
            self._apply_to(row, "reference")

    def action_fix_placeholder(self) -> None:
        if row := self._open_current():
            self._apply_to(row, "placeholder")

    def action_unstage(self) -> None:
        row = self._open_current()
        if row is None:
            return
        finding = row.decision.finding
        try:
            actions = applier.unstage_and_ignore(finding.path)
        except Exception as exc:                       # git refused: say so, change nothing
            self.notify(str(exc), severity="error", timeout=8, markup=False)
            return
        for other in self.rows:                        # the whole file is out of the commit
            if other.decision.finding.path == finding.path and other.state == "open":
                other.state, other.note = "fixed", "; ".join(actions)
        self._resolve(row, "fixed", "; ".join(actions), "unstage_ignore")

    def action_exception(self) -> None:
        row = self._open_current()
        if row is None:
            return
        finding = row.decision.finding

        def record(reason: str | None) -> None:
            if not reason:
                return
            audit_exceptions.add(of_finding(finding), reason, self.cfg.exceptions_ttl_days,
                                 rule_id=finding.rule_id, path=finding.path)
            self._resolve(row, "excepted",
                          f"Exception for {self.cfg.exceptions_ttl_days} days: {reason}. It is "
                          "local to you until `zerotrace exceptions -i` promotes it for review.",
                          "exception")

        self.push_screen(ReasonScreen(), record)

    def action_fix_all(self) -> None:
        """Apply the env/vault rewrite to every open finding that has one.

        A long review is mostly the same decision repeated, but it still changes several
        files at once, so it asks first and reports exactly how many it could not do.
        """
        candidates = [row for row in self.rows if row.state == "open"
                      and proposer.propose(row.decision, "reference",
                                           self.cfg).mode not in ("unstage", "manual")]
        if not candidates:
            self.notify("Nothing here can be rewritten automatically.", severity="warning")
            return

        def go(confirmed: bool | None) -> None:
            if not confirmed:
                return
            fixed = sum(1 for row in candidates if self._apply_to(row, "reference", quiet=True))
            self.redraw()
            self._advance()
            missed = len(candidates) - fixed
            self.notify(f"{fixed} rewritten"
                        + (f", {missed} could not be applied — review them individually."
                           if missed else "."),
                        severity="warning" if missed else "information", timeout=8)

        self.push_screen(
            ConfirmScreen(f"Rewrite {len(candidates)} finding(s) as environment or vault "
                          "references? The staged copy changes; your unstaged edits do not."),
            go)

    def action_toggle_resolved(self) -> None:
        """Hide what is already done. On a big changeset the open ones are what matter."""
        self.hide_resolved = not self.hide_resolved
        self.redraw(keep=self.current())


def review(decisions: list, cfg) -> int:
    """Run the review app. Returns 0 if every finding was resolved, 1 otherwise."""
    return run_app(ReviewApp(decisions, cfg))

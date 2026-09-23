"""rich rendering + interaction, with a headless fallback. Raw values are never printed."""
import sys

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..audit import exceptions as audit_exceptions
from ..audit import log as audit_log
from ..audit.fingerprint import of_finding
from ..classifier.redact import language_of, redact
from . import glyphs, menu, theme
from ..policy.engine import Decision, by_priority
from ..remediation import applier, proposer
from . import logo as logo_render

console = Console(stderr=False, highlight=False)

_SEVERITY_STYLE = {
    "critical": "bold white on red",
    "high": "bold red",
    "medium": "yellow",
    "low": "dim",
}
_ACTION_STYLE = {"block": "bold red", "warn": "yellow", "allow": "green"}
_LEXER = {"python": "python", "javascript": "javascript", "typescript": "typescript",
          "go": "go", "java": "java", "yaml": "yaml", "json": "json", "terraform": "terraform",
          "dotenv": "ini", "ini": "ini", "toml": "toml", "shell": "bash",
          "dockerfile": "docker", "csharp": "csharp", "ruby": "ruby", "php": "php",
          "rust": "rust", "kotlin": "kotlin", "xml": "xml", "markdown": "markdown"}


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# Every value detected in the current changeset, so a line that carries two findings
# never shows the other one in clear text.
_ALL_VALUES: dict[str, str] = {}


def _remember(decisions) -> None:
    for d in decisions:
        f = d.finding
        if f.matched_value:
            _ALL_VALUES[f.matched_value] = redact(f.matched_value, f.kind)


def _masked(text: str, finding) -> str:
    """Never render a raw secret/PII value; show a typed, length-hinted token."""
    if finding.matched_value:
        text = text.replace(finding.matched_value, redact(finding.matched_value, finding.kind))
    for value in sorted(_ALL_VALUES, key=len, reverse=True):
        text = text.replace(value, _ALL_VALUES[value])
    return text


def _where(finding) -> str:
    return f"{finding.path}:{finding.line_no}" if finding.line_no else f"{finding.path} (whole file)"


def decided_by(decision) -> str:
    """Where the verdict came from. Shown in both the inline table and the full-screen app."""
    if decision.model_verdict is not None:
        return "AI tie-break"
    return "policy (exception)" if "exception" in decision.reason else "deterministic"


def _verdict_line(decisions) -> str:
    """One sentence a developer can act on, under the table."""
    blocked = sum(1 for d in decisions if d.action == "block")
    warned = sum(1 for d in decisions if d.action == "warn")
    parts = []
    if blocked:
        parts.append(f"[bold red]{blocked} blocking[/]")
    if warned:
        parts.append(f"[yellow]{warned} to confirm[/]")
    marks = glyphs.for_console(console)
    summary = f" {marks['dot']} ".join(parts) or "nothing blocking"
    return glyphs.sanitize(
        f"{summary} {marks['dash']} the commit is held until each one is fixed, "
        "excepted, or abandoned", console)


def _summary_table(decisions) -> Table:
    table = Table(title="Staged findings", title_style="bold", expand=False,
                  box=glyphs.box_for(console), caption=_verdict_line(decisions),
                  caption_justify="left", row_styles=["", "on grey11"])
    # Under ~100 columns the "decided by" column is what pushes the location into wrapping,
    # and a path broken across two lines is not clickable in any terminal. It is dropped
    # first; it is also repeated in the panel under the table.
    roomy = console.width >= 100
    table.add_column("#", justify="right", style="dim", width=2)
    table.add_column("", width=1)                       # severity accent bar
    table.add_column("Location", overflow="ellipsis", no_wrap=True, style="bold",
                     min_width=18)
    table.add_column("Rule", overflow="ellipsis", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    if roomy:
        table.add_column("Decided by", style="dim", no_wrap=True)
    table.add_column("Action", justify="center", no_wrap=True)
    for index, decision in enumerate(decisions, start=1):
        f = decision.finding
        sev = _SEVERITY_STYLE.get(f.severity, "")
        act = _ACTION_STYLE.get(decision.action, "")
        bar = glyphs.for_console(console)["bar"]
        # Paths, rule ids and staged text are data: `app/[slug]/page.tsx` would otherwise
        # lose its directory to markup, and a stray `[/]` would raise mid-commit.
        cells = [str(index), f"[{sev.split()[-1]}]{bar}[/]", escape(_where(f)), escape(f.rule_id),
                 f"[{sev}]{f.severity}[/]"]
        if roomy:
            cells.append(escape(decided_by(decision)))
        cells.append(f"[{act}]{decision.action.upper()}[/]")
        table.add_row(*cells)
    return table


def _finding_panel(decision) -> Panel:
    f = decision.finding
    style = _SEVERITY_STYLE.get(f.severity, "")
    parts: list = [Text.from_markup(f"[bold]{escape(f.rule_id)}[/] ({escape(f.severity)})")]
    if f.explanation:
        parts.append(Text(f.explanation))
    parts.append(Text(f"Decision: {decision.reason}", style="italic"))
    if f.line_text:
        parts += ["", Syntax(_masked(f.line_text, f), _LEXER.get(language_of(f.path), "text"),
                             theme="ansi_dark", line_numbers=True, start_line=f.line_no)]
    return Panel(Group(*parts), box=glyphs.box_for(console),
                 title=f"[{_ACTION_STYLE.get(decision.action, '')}]{decision.action.upper()}[/] "
                       f"{escape(_where(f))}",
                 border_style=style.split()[-1] if style else "white")


def _fix_hint(decision, cfg) -> str:
    p = proposer.propose(decision, "reference", cfg)
    if p.mode in ("unstage", "manual"):
        return p.note
    return f"suggested: {_masked(p.new_line or '', decision.finding).strip()}"


def headless_report(decisions, cfg=None) -> None:
    decisions = by_priority(decisions)       # the table and the panels in the same order
    _remember(decisions)
    console.print(_summary_table(decisions))
    for decision in decisions:
        console.print(_finding_panel(decision))
        console.print(f"  [green]fix[/] {escape(_fix_hint(decision, cfg))}")


def _preview(decision, proposal) -> Panel:
    f = decision.finding
    if proposal.mode in ("unstage", "manual"):
        title = "Proposed fix" if proposal.mode == "unstage" else "Fix this by hand"
        return Panel(Text(proposal.note), title=title, border_style="green",
                     box=glyphs.box_for(console))
    old = _masked(f.line_text, f)
    body = Text()
    body.append(f"- {old}\n", style="red")
    body.append(f"+ {_masked(proposal.new_line or '', f)}", style="green")
    if proposal.note:
        body.append(f"\n\n{proposal.note}", style="dim")
    return Panel(body, title="Proposed fix (applied to the staged copy only)",
                 border_style="green", box=glyphs.box_for(console))


def _apply_unstage(finding, cfg, resolved_paths: set[str]) -> bool:
    for action in applier.unstage_and_ignore(finding.path):
        console.print(f"  [green]{glyphs.for_console(console)['ok']}[/] {escape(action)}")
    resolved_paths.add(finding.path)
    audit_log.append({"fingerprint": of_finding(finding), "path": finding.path,
                      "action": "remediated", "method": "unstage_ignore"})
    return True


def _apply_fix(finding, cfg, mode: str) -> bool:
    proposal = proposer.propose(Decision("block", finding.severity, "", finding), mode, cfg)
    try:
        where = applier.apply(finding.path, finding.line_no, proposal.new_line or "",
                              finding.line_text)
    except applier.StaleIndexError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return False
    audit_log.append({"fingerprint": of_finding(finding), "path": finding.path,
                      "action": "remediated",
                      "method": "vault_reference" if mode == "reference" else "placeholder"})
    tick = glyphs.for_console(console)["ok"]
    console.print(f"[green]{tick} Fix applied to the {where.replace('+', ' and ')} "
                  "and re-staged.[/green]")
    return True


def _record_exception(finding, cfg) -> bool:
    reason = Prompt.ask("Reason for this exception (recorded in the audit log)")
    if not reason.strip():
        console.print("[red]An exception needs a reason.[/red]")
        return False
    fingerprint = of_finding(finding)
    audit_exceptions.add(fingerprint, reason, cfg.exceptions_ttl_days,
                         rule_id=finding.rule_id, path=finding.path)
    audit_log.append({"fingerprint": fingerprint, "path": finding.path,
                      "action": "exception", "reason": reason})
    console.print(f"[yellow]Exception recorded for {cfg.exceptions_ttl_days} days "
                  "(scoped to this exact line).[/yellow]")
    console.print("[dim]It is local to you. `zerotrace exceptions -i` (or `--promote`) moves it "
                  f"into {audit_exceptions.SHARED_FILE} so a reviewer sees it in the PR.[/]")
    return True


_UNSTAGE = menu.Option("u", "unstage the file and add it to .gitignore")
_REFERENCE = menu.Option("v", "env/vault reference")
_PLACEHOLDER = menu.Option("r", "safe placeholder")
_EXCEPTION = menu.Option("e", "exception, with a written reason")
_ABORT = menu.Option("a", "abort the commit")


def _offer_choices(decision, cfg) -> list[menu.Option]:
    """Print the preview(s) for this finding and return what the developer may choose.

    The recommended fix comes first, because the menu opens with the first option highlighted.
    """
    finding = decision.finding
    if finding.line_no == 0:
        console.print(_preview(decision, proposer.propose(decision, "unstage", cfg)))
        return [_UNSTAGE, _EXCEPTION, _ABORT]
    reference = proposer.propose(decision, "reference", cfg)
    console.print(_preview(decision, reference))
    if reference.mode == "manual":
        # Nothing to rewrite automatically (e.g. a value assembled from parts).
        return [_EXCEPTION, _ABORT]
    placeholder = proposer.propose(decision, "placeholder", cfg)
    safe = escape(_masked(placeholder.new_line or "", finding).strip())
    console.print(f"  [dim]or R: {safe}[/]")
    return [_REFERENCE, _PLACEHOLDER, _EXCEPTION, _ABORT]


def _interactive_resolve(decision, cfg, resolved_paths: set[str]) -> bool:
    """Returns True if this finding is resolved (fixed or excepted), False if aborted."""
    finding = decision.finding
    console.print(_finding_panel(decision))
    options = _offer_choices(decision, cfg)
    # Arrow keys and Enter, the letter in either case, or a click. Falls back to a typed
    # prompt when the terminal cannot draw the menu.
    choice = menu.choose("How should this finding be resolved?", options,
                         default=options[0].key, console=console)

    if choice == "u":
        return _apply_unstage(finding, cfg, resolved_paths)
    if choice in ("v", "r"):
        return _apply_fix(finding, cfg, "reference" if choice == "v" else "placeholder")
    if choice == "e":
        return _record_exception(finding, cfg)
    console.print("[red]Aborted. Fix manually and re-stage before committing.[/red]")
    return False


def banner() -> None:
    art = logo_render.render(console)
    if art:
        sys.stdout.write(art if art.endswith("\n") else art + "\n")
        sys.stdout.flush()
    console.print(Panel.fit(
        glyphs.sanitize(f"[{theme.ACCENT_BOLD}]ZeroTrace[/] · secret & PII guardrail · "
                        "local-first", console),
        border_style=theme.ACCENT_STYLE, box=glyphs.box_for(console),
    ))


def present(decisions, cfg, interactive: bool = True) -> int:
    """Show the blocking findings and, interactively, resolve them one by one.

    No banner: the logo belongs to the first install, not to every blocked commit. The table
    and the panels under it follow the same priority order (policy.engine.by_priority).
    """
    decisions = by_priority(decisions)
    if not interactive:
        headless_report(decisions, cfg)
        return 1

    _remember(decisions)
    console.print(_summary_table(decisions))
    resolved_paths: set[str] = set()
    touched: set[tuple[str, int]] = set()
    deferred = 0
    for decision in decisions:
        f = decision.finding
        if f.path in resolved_paths:
            continue  # the whole file was already unstaged
        if (f.path, f.line_no) in touched:
            deferred += 1  # line already rewritten; the re-scan re-checks it
            continue
        if not _interactive_resolve(decision, cfg, resolved_paths):
            return 1  # abort stops the whole commit; nothing further is applied
        touched.add((f.path, f.line_no))
    if deferred:
        console.print(f"[dim]Re-scanning {deferred} finding(s) on lines that were just rewritten…[/]")
    return 0

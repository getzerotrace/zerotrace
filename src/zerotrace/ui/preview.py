"""`zerotrace ui`: render every screen so a terminal can be checked in one command.

Run it in each console you care about (Windows Terminal, legacy cmd.exe, PowerShell 5.1,
the VS Code / JetBrains integrated terminals, an SSH session, CI logs) and look for
mojibake, clipped tables or missing colour. It renders through the same code paths the
real commands use, so what you see here is what a developer sees at commit time.
"""
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import Config
from ..detectors import Finding
from ..policy.engine import Decision
from . import capability, logo, terminal, theme
from .progress import Bar

_TIERS = ("auto", "png", "card", "unicode", "ascii", "text")


def _sample_decisions() -> list[Decision]:
    """Fabricated findings: no scan, no repo needed, and no real values on screen."""
    samples = [
        ("src/payments/charge.py", 4, "stripe-live-key", "critical", "block",
         'stripe.api_key = "sk_live_EXAMPLEEXAMPLEEXAMPLE"', "sk_live_EXAMPLEEXAMPLEEXAMPLE",
         "stripe_live_key", "Stripe live key: can move money."),
        ("config/app.yml", 12, "hardcoded-secret", "medium", "warn",
         '  session_secret: k9s-dev-2024', "k9s-dev-2024", "hardcoded_secret",
         "Ambiguous value: sent to the local model."),
        (".env", 0, "sensitive-file", "high", "block", "", "", "sensitive_file",
         "Environment file: unstage it and commit .env.example instead."),
    ]
    out = []
    for path, line_no, rule, severity, action, text, value, kind, why in samples:
        finding = Finding(rule_id=rule, kind=kind, severity=severity, confidence=0.9, path=path,
                          line_no=line_no, file_class="code", line_text=text,
                          context_snippet=text, source="rulepack", explanation=why,
                          matched_value=value)
        out.append(Decision(action, severity, f"{rule} matched in {path}", finding))
    return out


def _capabilities(console: Console) -> Table:
    table = Table(title="Terminal capabilities", show_header=False)
    table.add_column("Check", style="bold")
    table.add_column("Value", overflow="fold")
    rows = [
        ("platform", f"{sys.platform} · python {sys.version.split()[0]}"),
        ("is a terminal", str(console.is_terminal)),
        ("size", f"{console.width}x{console.height}"),
        ("colour system", str(console.color_system)),
        ("stdout encoding", str(getattr(sys.stdout, "encoding", "?"))),
        ("unicode art", str(capability.supports_unicode(console))),
        ("inline images", capability.why_no_image(console)),
        ("decorations allowed", str(capability.decorations_allowed())),
    ]
    env = {k: os.environ.get(k) for k in
           ("TERM", "TERM_PROGRAM", "WT_SESSION", "ConEmuANSI", "CI", "NO_COLOR",
            "ZEROTRACE_LOGO")}
    rows.append(("environment", ", ".join(f"{k}={v}" for k, v in env.items() if v) or "(none set)"))
    for check, value in rows:
        table.add_row(check, value)
    return table


def _section(console: Console, title: str) -> None:
    console.rule(f"[bold]{title}")


def run(tier: str = "auto") -> int:
    """Render every screen once. `tier` forces a logo tier; "all" loops over them."""
    console = Console()
    if tier == "all":
        for one in _TIERS:
            run(one)
        return 0

    previous = os.environ.get("ZEROTRACE_LOGO")
    if tier != "auto":
        os.environ["ZEROTRACE_LOGO"] = tier
    try:
        _section(console, f"1. logo (tier: {tier})")
        art = logo.render(console)
        if art:
            sys.stdout.write(art if art.endswith("\n") else art + "\n")
            sys.stdout.flush()

        _section(console, "2. capabilities")
        console.print(_capabilities(console))

        _section(console, "3. findings report (what a blocked commit looks like)")
        decisions = _sample_decisions()
        terminal.headless_report(decisions, Config())

        _section(console, "4. fix preview and menu")
        from . import menu
        from .terminal import _offer_choices
        options = _offer_choices(decisions[0], Config())
        console.print(menu.preview("How should this finding be resolved?", options, console))
        console.print("[dim](not asking: this is a preview)[/]")

        _section(console, "5. install progress bar")
        bar = Bar(4)
        for label in ("writing hook shims", "chaining existing hooks",
                      "setting core.hooksPath", "verifying"):
            bar.step(label)
        bar.finish()

        _section(console, "6. panels and status glyphs")
        console.print(Panel.fit(f"[{theme.ACCENT_BOLD}]ZeroTrace[/] · preview panel",
                                border_style=theme.ACCENT_STYLE))
        console.print("[green]✓[/] ok   [yellow]![/] warn   [red]✗[/] fail   "
                      "— if those show as ?, this console lacks UTF-8")
        console.print("[dim]Run `zerotrace ui --tier ascii` to see the legacy-console rendering.[/]")
        return 0
    finally:
        if tier != "auto":
            if previous is None:
                os.environ.pop("ZEROTRACE_LOGO", None)
            else:
                os.environ["ZEROTRACE_LOGO"] = previous

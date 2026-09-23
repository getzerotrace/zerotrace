"""Every screen, in every console we claim to support.

The matrix that matters in practice:
  utf-8 + colour     Windows Terminal, iTerm2, GNOME Terminal, VS Code, JetBrains
  cp437 / cp1252     legacy cmd.exe and PowerShell 5.1 consoles
  ascii / no colour   CI logs, `| tee`, dumb terminals, NO_COLOR
A screen must never raise on any of them, and must never emit a character the destination
cannot encode (which would print as "?" and read like a bug).
"""
import io
import re

import pytest
from rich.console import Console

from zerotrace.config import Config
from zerotrace.detectors import Finding
from zerotrace.policy.engine import Decision
from zerotrace.ui import glyphs, logo, preview, terminal
from zerotrace.ui.progress import Bar

_ENCODINGS = ["utf-8", "cp437", "cp1252", "ascii"]
_WIDTHS = [200, 120, 100, 80, 60, 40]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("ZEROTRACE_LOGO", "KITTY_WINDOW_ID", "TERM", "TERM_PROGRAM", "CI", "NO_COLOR"):
        monkeypatch.delenv(key, raising=False)


def _stream(encoding: str) -> io.TextIOWrapper:
    """A stream that behaves like a console of that codepage: strict, so a bad glyph raises."""
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict", write_through=True)


def _console(encoding: str, width: int = 100, color: str | None = "truecolor") -> Console:
    return Console(file=_stream(encoding), force_terminal=True, width=width, color_system=color)


def _decisions() -> list[Decision]:
    return preview._sample_decisions()


def _rendered(console: Console) -> str:
    console.file.flush()
    return console.file.buffer.getvalue().decode(console.encoding, "replace")


# --- the screens ----------------------------------------------------------------------

@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_logo_never_emits_an_unencodable_character(encoding, monkeypatch):
    console = _console(encoding)
    art = logo.render(console)
    art.encode(encoding)                       # raises if the console could not print it


@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_findings_report_renders(encoding, monkeypatch):
    console = _console(encoding)
    monkeypatch.setattr(terminal, "console", console)
    terminal.headless_report(_decisions(), Config())
    out = _rendered(console)
    assert "stripe-live-key" in out
    assert "sk_live_EXAMPLEEXAMPLEEXAMPLE" not in out      # masked even in the preview


@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_fix_preview_and_banner_render(encoding, monkeypatch):
    console = _console(encoding)
    monkeypatch.setattr(terminal, "console", console)
    terminal.banner()
    terminal._offer_choices(_decisions()[0], Config())
    assert "ZeroTrace" in _rendered(console)


@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_progress_bar_renders(encoding):
    stream = _stream(encoding)
    bar = Bar(3, file=stream, console=Console(file=stream, force_terminal=True))
    for label in ("one", "two", "three"):
        bar.step(label)
    bar.finish()
    stream.flush()


@pytest.mark.parametrize("encoding", _ENCODINGS)
def test_doctor_table_renders(encoding):
    from zerotrace.doctor import FAIL, OK, WARN, Report
    console = _console(encoding)
    report = Report()
    report.add(OK, "python", "3.12.0")
    report.add(WARN, "model", "not reachable · retry")
    report.add(FAIL, "hooks", "not installed…")
    console.print(report.table(console))
    out = _rendered(console)
    assert "python" in out and "?" not in out.replace("?)", "")


# --- widths ---------------------------------------------------------------------------

@pytest.mark.parametrize("width", _WIDTHS)
def test_no_screen_overflows_the_terminal_width(width, monkeypatch):
    console = _console("utf-8", width=width)
    monkeypatch.setattr(terminal, "console", console)
    terminal.banner()
    terminal.headless_report(_decisions(), Config())
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    for line in _rendered(console).splitlines():
        visible = ansi.sub("", line)
        assert len(visible) <= width, f"{len(visible)} > {width}: {visible[:60]}"


# --- colour and capability rules --------------------------------------------------------

def test_no_color_env_strips_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    art = logo.render(_console("utf-8"))
    assert "\033[" not in art


def test_redirected_output_gets_no_image_escapes(monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    console = Console(file=_stream("utf-8"), force_terminal=False, width=100)
    assert "\033_G" not in (logo.render(console) or "")


def test_glyphs_downgrade_only_where_needed():
    utf8, legacy = _console("utf-8"), _console("cp437")
    assert glyphs.for_console(utf8)["ok"] == "✓"
    assert glyphs.for_console(legacy)["ok"] == "+"
    assert glyphs.sanitize("done… ✓ · ok", legacy) == "done... + - ok"
    assert glyphs.sanitize("done… ✓", utf8) == "done… ✓"


# --- the `zerotrace ui` command itself ---------------------------------------------------

@pytest.mark.parametrize("tier", ["auto", "unicode", "ascii", "text"])
def test_ui_preview_command_runs_for_every_tier(tier, capsys):
    assert preview.run(tier) == 0
    out = capsys.readouterr().out
    assert "capabilities" in out and "ZEROTRACE" in out.upper()


def test_ui_preview_masks_the_sample_secret(capsys):
    preview.run("ascii")
    assert "sk_live_EXAMPLEEXAMPLEEXAMPLE" not in capsys.readouterr().out


def test_sample_findings_are_obviously_fake():
    """The preview must never need a repo or a scan, and never show a realistic secret."""
    allowed = {"", "sk_live_EXAMPLEEXAMPLEEXAMPLE", "k9s-dev-2024"}
    for decision in _decisions():
        assert isinstance(decision.finding, Finding)
        assert decision.finding.matched_value in allowed

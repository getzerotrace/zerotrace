"""The install progress bar: one pinned line with a gradient, degrading to plain log lines.

Shape borrowed from the yeet installer: bar last, log above it, fixed label field, ASCII
fallback, nothing but plain lines when the destination is not a terminal.
"""
import io
import re

import pytest
from rich.console import Console

from zerotrace.ui import progress
from zerotrace.ui.progress import Bar

_ENV_KEYS = ("KITTY_WINDOW_ID", "TERM", "TERM_PROGRAM", "CI", "NO_COLOR")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # This suite's own CI sets CI=true, which would otherwise force every coloured-tier
    # test below into the plain fallback.
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _fixed_width(monkeypatch):
    monkeypatch.setattr(progress.shutil, "get_terminal_size",
                        lambda fallback=(80, 24): type("S", (), {"columns": 100, "lines": 24})())


class _FakeTTY(io.StringIO):
    """A terminal-like stream: rich reads .encoding to decide what glyphs are safe."""

    def __init__(self, encoding: str = "utf-8"):
        super().__init__()
        self._encoding = encoding

    def isatty(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return self._encoding


def _console(file, *, color: str | None = "truecolor") -> Console:
    return Console(file=file, force_terminal=True, color_system=color)


def _last_frame(out: io.StringIO) -> str:
    return out.getvalue().split("\r")[-1]


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


# --- degradation ------------------------------------------------------------------------

def test_non_tty_prints_discrete_step_lines_and_never_uses_carriage_return():
    out = io.StringIO()
    bar = Bar(3, file=out)
    bar.step("first")
    bar.step("second")
    bar.finish()
    assert "\r" not in out.getvalue()
    assert out.getvalue().splitlines() == ["zerotrace: [1/3] first", "zerotrace: [2/3] second"]


def test_tty_without_unicode_uses_ascii_blocks():
    out = _FakeTTY(encoding="cp437")
    bar = Bar(4, file=out, console=_console(out, color=None))
    bar.step("writing hooks")
    frame = _plain(_last_frame(out))
    assert "#" in frame and "█" not in frame
    frame.encode("cp437")                       # nothing the console cannot render


def test_tty_with_unicode_uses_block_characters():
    out = _FakeTTY()
    bar = Bar(4, file=out, console=_console(out))
    bar.step("writing hooks")
    assert "█" in _last_frame(out)


def test_colour_is_dropped_when_the_console_has_none():
    out = _FakeTTY()
    Bar(2, file=out, console=_console(out, color=None)).step("x")
    assert "\033[38;2;" not in out.getvalue()


def test_gradient_is_used_when_colour_is_available():
    out = _FakeTTY()
    bar = Bar(2, file=out, console=_console(out))
    bar.step("x")
    assert out.getvalue().count("\033[38;2;") > 1, "the fill should walk several colour stops"


# --- layout ------------------------------------------------------------------------------

def test_bar_fills_proportionally():
    out = _FakeTTY()
    bar = Bar(4, file=out, console=_console(out))
    bar.step("quarter")
    quarter = _plain(_last_frame(out)).count("█")
    bar.step("half")
    half = _plain(_last_frame(out)).count("█")
    assert 0 < quarter < half


def test_finish_leaves_a_full_bar_and_a_newline():
    out = _FakeTTY()
    bar = Bar(3, file=out, console=_console(out))
    bar.step("one")
    bar.finish()
    final = _plain(out.getvalue().split("\r")[-1])
    assert "100%" in final and final.endswith("\n")
    assert "░" not in final, "a finished bar should be full"


def test_label_is_shown_and_truncated_to_a_fixed_field():
    out = _FakeTTY()
    bar = Bar(2, file=out, console=_console(out))
    bar.step("Registering core.hooksPath and a great deal more text than fits")
    frame = _plain(_last_frame(out))
    assert "Registering core.hooksPath" in frame
    assert len(frame.rstrip()) <= 100


def test_narrow_terminal_drops_the_label_but_keeps_the_bar(monkeypatch):
    monkeypatch.setattr(progress.shutil, "get_terminal_size",
                        lambda fallback=(80, 24): type("S", (), {"columns": 40, "lines": 24})())
    out = _FakeTTY()
    bar = Bar(2, file=out, console=_console(out))
    bar.step("Registering core.hooksPath")
    frame = _plain(_last_frame(out))
    assert "Registering" not in frame
    assert "%" in frame and len(frame.rstrip()) <= 40


@pytest.mark.parametrize("columns", [200, 120, 100, 80, 60, 50, 40, 30, 20])
def test_no_width_overflows_the_window(columns, monkeypatch):
    monkeypatch.setattr(progress.shutil, "get_terminal_size",
                        lambda fallback=(80, 24): type("S", (), {"columns": columns})())
    out = _FakeTTY()
    bar = Bar(3, file=out, console=_console(out))
    bar.step("Writing hook shims")
    assert len(_plain(_last_frame(out)).rstrip()) <= columns


# --- logging above the bar ----------------------------------------------------------------

def test_log_prints_above_the_bar_and_redraws_it():
    out = _FakeTTY()
    bar = Bar(3, file=out, console=_console(out))
    bar.step("one")
    bar.log("zerotrace: wrote 16 hook shims")
    text = out.getvalue()
    assert "zerotrace: wrote 16 hook shims\n" in text
    assert text.index("wrote 16 hook shims") < text.rindex("%"), "the bar is redrawn after the log"
    assert "\033[K" in text, "the bar line is erased before the log is written"


def test_log_on_a_non_tty_just_prints():
    out = io.StringIO()
    bar = Bar(2, file=out)
    bar.log("plain line")
    assert out.getvalue() == "plain line\n"

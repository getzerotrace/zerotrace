"""The scanning spinner: a themed line while a scan runs, and nothing where it cannot draw."""
import io
import re

import pytest
from rich.console import Console

from zerotrace.ui import spinner
from zerotrace.ui.spinner import WORDS, Spinner, render_line

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("TERM", "CI", "NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE"):
        monkeypatch.delenv(key, raising=False)


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
    return Console(file=file, force_terminal=True, color_system=color, width=100)


def _drawn(text: str) -> str:
    return _ANSI.sub("", text)


# --- what it draws ------------------------------------------------------------------------

def test_a_terminal_sees_the_word_and_the_line_is_erased_afterwards():
    out = _FakeTTY()
    with Spinner("scanning 3 staged files", console=_console(out)):
        pass
    drawn = _drawn(out.getvalue())
    assert any(word in drawn for word in WORDS)
    assert "zerotrace:" in drawn
    assert "scanning 3 staged files" in drawn
    assert out.getvalue().endswith("\x1b[2K"), "the last thing sent must erase the line"


def test_the_note_can_change_while_it_runs():
    out = _FakeTTY()
    with Spinner("reading the staged diff", console=_console(out)) as spin:
        spin.detail("scanning 12 staged files")
    assert "scanning 12 staged files" in _drawn(out.getvalue())


def test_the_default_console_is_stderr(monkeypatch):
    fake = _FakeTTY()
    monkeypatch.setattr("sys.stderr", fake)
    with Spinner("x"):
        pass
    assert any(word in fake.getvalue() for word in WORDS)


def test_the_line_is_erased_even_when_the_scan_raises():
    out = _FakeTTY()
    with pytest.raises(RuntimeError), Spinner("x", console=_console(out)):
        raise RuntimeError("detector exploded")
    assert out.getvalue().endswith("\x1b[2K")


# --- where it must stay silent -------------------------------------------------------------

def test_nothing_is_drawn_when_output_is_not_a_terminal():
    out = io.StringIO()
    with Spinner("x", console=Console(file=out, width=80)):
        pass
    assert out.getvalue() == ""


def test_a_dumb_terminal_gets_nothing(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    out = _FakeTTY()
    with Spinner("x", console=_console(out)):
        pass
    assert out.getvalue() == ""


# --- the frame ----------------------------------------------------------------------------

def test_the_glyph_cycles_and_its_colour_pulses_through_the_theme_ramp():
    console = _console(_FakeTTY())
    glyphs_seen = {render_line(console, "Word", tick).plain[len("zerotrace: ")]
                   for tick in range(10)}
    assert len(glyphs_seen) == 10
    colours = {spinner._glow(tick) for tick in range(22)}
    assert len(colours) == len(spinner.theme.RAMP_RGB)
    assert all(re.fullmatch(r"#[0-9a-f]{6}", colour) for colour in colours)


@pytest.mark.parametrize(("seconds", "shown"), [(1.2, "1s"), (59.9, "59s"), (65, "1m 05s"),
                                                (600, "10m 00s")])
def test_elapsed_time_is_shown_from_the_first_second(seconds, shown):
    line = render_line(_console(_FakeTTY()), "Word", 0, "scanning", seconds).plain
    assert line.endswith(f"scanning · {shown}")


def test_no_elapsed_time_before_the_first_second():
    assert render_line(_console(_FakeTTY()), "Word", 0, "", 0.4).plain.endswith("Word…")


def test_a_legacy_console_gets_ascii_only():
    out = _FakeTTY(encoding="cp437")
    line = render_line(_console(out, color=None), "Lollygagging", 3, "scanning 2 files", 65).plain
    line.encode("cp437")                        # nothing the console cannot print
    assert "..." in line
    assert "…" not in line
    assert " - " in line


def test_a_long_line_is_cut_rather_than_wrapped():
    narrow = Console(file=_FakeTTY(), force_terminal=True, color_system=None, width=30)
    with narrow.capture() as captured:
        narrow.print(spinner._Line("scanning 11 staged files in this repository", lambda: 0.0))
    assert len(captured.get().splitlines()) == 1


# --- the words ----------------------------------------------------------------------------

def test_the_words_are_distinct_and_fit_on_a_line():
    assert len(set(WORDS)) == len(WORDS) >= 30
    assert "Lollygagging" in WORDS
    assert all(word.isascii() and 6 <= len(word) <= 22 for word in WORDS)


def test_the_word_changes_every_few_seconds_and_wraps_around():
    now = [100.0]
    line = spinner._Line("", lambda: now[0])
    console = _console(_FakeTTY(), color=None)
    for step in (0, 1, 2, len(WORDS)):
        now[0] = 100.0 + step * spinner._WORD_SECONDS + 0.01
        text = next(iter(line.__rich_console__(console, console.options))).plain
        assert WORDS[(line._first + step) % len(WORDS)] in text

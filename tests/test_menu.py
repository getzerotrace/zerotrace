"""The inline fix menu: arrow keys and Enter, the letter in either case, or a mouse click.

The first group drives the menu headlessly with the same byte sequences a terminal sends,
mouse reports included. The last group runs the real hook flow in a pseudo-terminal, so the
terminal handshake (cursor-position report, mouse tracking) is exercised end to end.
"""
import io
import os
import select
import subprocess
import sys
import time

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from zerotrace.ui import menu
from zerotrace.ui.menu import Option

from .conftest import git, write

OPTIONS = [Option("v", "env/vault reference"), Option("r", "safe placeholder"),
           Option("e", "exception"), Option("a", "abort")]
UP, DOWN, ENTER = "\x1b[A", "\x1b[B", "\r"


def _answer(keys: str, options=OPTIONS, default: str | None = "v") -> str:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        return menu.build("How?", options, default, input=pipe, output=DummyOutput()).run()


def _click(row: int, column: int = 6) -> str:
    """An SGR mouse press and release. Rows are 1-based; row 1 is the question."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


@pytest.mark.parametrize("keys,chosen", [
    (ENTER, "v"),                           # the recommended fix is highlighted first
    (DOWN + ENTER, "r"),
    (DOWN + DOWN + DOWN + ENTER, "a"),
    (DOWN * 9 + ENTER, "a"),                # stops at the bottom rather than wrapping
    (UP + ENTER, "v"),                      # and at the top
    (DOWN + DOWN + UP + ENTER, "r"),
    ("jj" + ENTER, "e"),                    # vim keys, as in the full-screen apps
    ("\t" + ENTER, "r"),                    # Tab and Shift+Tab too
])
def test_arrow_keys_and_enter_choose(keys, chosen):
    assert _answer(keys) == chosen


@pytest.mark.parametrize("letter", ["a", "A"])
def test_a_letter_moves_the_highlight_and_enter_confirms(letter):
    assert _answer(letter + ENTER) == "a"


def test_a_letter_alone_does_not_answer():
    """`v` then Enter works as before, and a stray key never answers the next question."""
    assert _answer("a" + UP + ENTER) == "e"


@pytest.mark.parametrize("row,chosen", [(2, "v"), (3, "r"), (4, "e"), (5, "a")])
def test_a_click_chooses_that_option(row, chosen):
    assert _answer(_click(row)) == chosen


def test_the_mouse_wheel_moves_the_highlight():
    wheel_down = "\x1b[<65;6;3M"
    assert _answer(wheel_down + wheel_down + ENTER) == "e"


def test_hovering_moves_the_highlight():
    hover_over_abort = "\x1b[<35;6;5M"
    assert _answer(hover_over_abort + ENTER) == "a"


def test_ctrl_c_interrupts_like_the_typed_prompt_did():
    with pytest.raises(KeyboardInterrupt):
        _answer("\x03")


def test_an_option_can_own_a_navigation_letter():
    """j/k navigate only when no option already uses the letter."""
    options = [Option("k", "keep"), Option("a", "abort")]
    assert _answer("k" + ENTER, options, default="a") == "k"


def test_the_default_decides_the_first_highlight():
    assert _answer(ENTER, default="e") == "e"
    assert _answer(ENTER, default="missing") == "v"


def test_an_empty_menu_is_refused():
    with pytest.raises(ValueError):
        menu.build("How?", [], None, input=None, output=DummyOutput())


def test_without_a_terminal_it_asks_by_typing(monkeypatch):
    """Piped stdin cannot drive a menu, so the old typed prompt answers instead."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("R\n"))
    console = Console(file=io.StringIO(), force_terminal=False)
    assert menu.choose("How?", OPTIONS, "v", console=console) == "r"


def test_a_dumb_terminal_is_not_drawn_on(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert menu._can_draw() is False


@pytest.mark.parametrize("encoding,pointer", [("utf-8", "❯"), ("cp437", ">"), ("ascii", ">")])
def test_the_preview_uses_glyphs_the_console_can_print(encoding, pointer):
    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
    console = Console(file=stream, force_terminal=True, width=80)
    console.print(menu.preview("How?", OPTIONS, console))
    stream.flush()
    rendered = stream.buffer.getvalue().decode(encoding)
    assert pointer in rendered and "safe placeholder" in rendered


# --- the real flow, in a pseudo-terminal ------------------------------------------------------

pty_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminals only")
_MENU_ROW = 20          # where the fake terminal says the cursor is when the menu draws


def _run_review_in_a_pty(keys: bytes) -> tuple[int, bytes]:
    """`zerotrace review --classic` on a 100x40 pty. Answers the cursor-position request the
    way a terminal would, then types `keys` once the menu is on screen."""
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = dict(os.environ, TERM="xterm-256color")
    child = subprocess.Popen([sys.executable, "-m", "zerotrace", "review", "--classic"],
                             stdin=slave, stdout=slave, stderr=slave, env=env,
                             start_new_session=True)
    os.close(slave)
    output, answered, typed, deadline = b"", False, False, time.monotonic() + 60
    try:
        while time.monotonic() < deadline and child.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:                 # the child closed the terminal
                break
            output += chunk
            if b"\x1b[6n" in chunk:
                os.write(master, f"\x1b[{_MENU_ROW};1R".encode())
                answered = True
            # Type only once the menu is drawn AND knows where it is: a click that arrives
            # before the position report cannot be mapped to an option.
            if not typed and answered and b"How should this finding be resolved" in output:
                time.sleep(0.3)
                os.write(master, keys)
                typed = True
        return child.wait(timeout=10), output
    finally:
        if child.poll() is None:
            child.kill()
        os.close(master)


def _stage_a_secret(fake) -> str:
    value = fake.stripe_live()
    write("pay.py", f'KEY = "{value}"\n')
    git("add", "-A")
    return value


@pty_only
def test_the_hook_menu_answers_arrow_keys_in_a_real_terminal(repo, fake):
    value = _stage_a_secret(fake)
    code, output = _run_review_in_a_pty(DOWN.encode() + b"\r")      # R: safe placeholder
    staged = git("show", ":pay.py").stdout
    assert code == 0
    assert value not in staged and staged.startswith('KEY = "<')
    assert value.encode() not in output, "no raw value is ever drawn"
    assert b"\x1b[?1000h" in output, "mouse reporting was switched on for the menu"


@pty_only
def test_the_hook_menu_answers_a_mouse_click_in_a_real_terminal(repo, fake):
    value = _stage_a_secret(fake)
    row_of_placeholder = _MENU_ROW + 2          # question, then V, then R
    click = f"\x1b[<0;8;{row_of_placeholder}M\x1b[<0;8;{row_of_placeholder}m".encode()
    code, _ = _run_review_in_a_pty(click)
    staged = git("show", ":pay.py").stdout
    assert code == 0
    assert value not in staged and staged.startswith('KEY = "<')

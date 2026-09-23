"""Pick one option inline: arrow keys and Enter, the option's letter, or a mouse click.

This is the fix menu the git hook shows under a finding. It draws a few lines, erases them
once answered and prints a one-line record of the choice, so the developer's scrollback stays
readable. It behaves the same on macOS, Linux and Windows: prompt_toolkit reads the legacy
Windows console through its native input API (mouse clicks included) and escape sequences
everywhere else.

It is imported only when a finding needs a decision, so a clean commit never pays for it, and
it falls back to a typed prompt whenever the terminal cannot draw it.
"""
import contextlib
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt
from rich.text import Text

from . import glyphs

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.mouse_events import MouseEvent

_STYLE = {"question": "bold", "selected": "reverse", "key": "bold", "hint": "#8a8a8a"}


@dataclass(frozen=True)
class Option:
    key: str            # the shortcut: one lower-case letter
    label: str


def _hint(console: Console) -> str:
    arrows = "↑/↓" if glyphs.encodable(console, "↑↓") else "up/down"
    return f"{arrows} and Enter, type the letter, or click"


def _pointer(console: Console) -> str:
    return "❯" if glyphs.encodable(console, "❯") else ">"


class _Menu:
    """The highlight and how every kind of input moves it. No terminal I/O in here."""

    def __init__(self, options: Sequence[Option], default: str | None) -> None:
        if not options:
            raise ValueError("a menu needs at least one option")
        self.options = list(options)
        keys = [option.key for option in self.options]
        self.index = keys.index(default) if default in keys else 0

    def move(self, step: int) -> None:
        """Up and down stop at the ends: wrapping from the top to Abort is a surprise."""
        self.index = max(0, min(self.index + step, len(self.options) - 1))

    def jump(self, key: str) -> None:
        """A letter moves the highlight and Enter confirms, as in the old typed prompt, so
        `v` then Enter still works and a stray key never answers the next question."""
        for index, option in enumerate(self.options):
            if option.key == key.lower():
                self.index = index

    @property
    def chosen(self) -> str:
        return self.options[self.index].key


def build(question: str, options: Sequence[Option], default: str | None = None, *,
          console: Console | None = None, input: Any = None, output: Any = None,
          ) -> "Application[str]":
    """The menu as a prompt_toolkit Application. `input`/`output` exist for the tests."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.styles import Style

    console = console or Console()
    menu = _Menu(options, default)
    pointer, hint = _pointer(console), _hint(console)

    def on_mouse(index: int, event: "MouseEvent") -> object:
        # Release, not press, like prompt_toolkit's own buttons: legacy mouse protocols
        # report a release without saying which button it was.
        if event.event_type == MouseEventType.MOUSE_UP:
            menu.index = index
            app.exit(result=menu.chosen)
        elif event.event_type in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_MOVE):
            menu.index = index
        elif event.event_type == MouseEventType.SCROLL_UP:
            menu.move(-1)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            menu.move(1)
        else:
            return NotImplemented
        return None

    def fragments() -> "StyleAndTextTuples":
        lines: StyleAndTextTuples = [("class:question", f"{question}\n")]
        for index, option in enumerate(options):
            selected = index == menu.index
            style = "class:selected" if selected else ""

            def handler(event: "MouseEvent", index: int = index) -> object:
                return on_mouse(index, event)

            lines += [(style, f"  {pointer if selected else ' '} ", handler),
                      (f"{style} class:key", option.key.upper(), handler),
                      (style, f"  {option.label}  ", handler),
                      ("", "\n")]
        lines.append(("class:hint", f"  {hint}"))
        return lines

    keys = KeyBindings()
    letters = {option.key.lower() for option in options}
    # j/k move too, as in the full-screen apps, unless an option already owns the letter.
    previous_keys = ["up", "s-tab", *(k for k in ("k", "K") if k.lower() not in letters)]
    next_keys = ["down", "tab", *(k for k in ("j", "J") if k.lower() not in letters)]

    for name in previous_keys:
        keys.add(name)(lambda event: menu.move(-1))
    for name in next_keys:
        keys.add(name)(lambda event: menu.move(1))

    @keys.add("enter")
    def _choose(event) -> None:
        event.app.exit(result=menu.chosen)

    @keys.add("c-c")
    def _interrupt(event) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    for option in options:
        for letter in {option.key.lower(), option.key.upper()}:
            keys.add(letter)(lambda event, key=option.key: menu.jump(key))

    control = FormattedTextControl(fragments, focusable=True, show_cursor=False)
    app: Application[str] = Application(
        layout=Layout(HSplit([Window(control, dont_extend_height=True,
                                     always_hide_cursor=True)])),
        key_bindings=keys, style=Style.from_dict(_STYLE), mouse_support=True,
        full_screen=False, erase_when_done=True, input=input, output=output)
    return app


def _can_draw() -> bool:
    """Both ends must be a terminal, and one that can move a cursor."""
    if os.environ.get("TERM", "") in ("dumb", "unknown"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):       # a closed or replaced stream
        return False


def _flush_typeahead() -> None:
    """Drop keys typed while the scan ran. An Enter pressed while waiting must not choose."""
    with contextlib.suppress(Exception):
        if sys.platform == "win32":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)


def typed(question: str, options: Sequence[Option], default: str | None = None,
          console: Console | None = None) -> str:
    """The same question as a typed prompt, for terminals that cannot draw the menu."""
    label = "  ".join(f"[bold]{option.key.upper()}[/] {escape(option.label)}"
                      for option in options)
    answer = Prompt.ask(f"{escape(question)}  {label}", choices=[o.key for o in options],
                        default=default, case_sensitive=False, console=console)
    return str(answer).lower()


def choose(question: str, options: Sequence[Option], default: str | None = None,
           console: Console | None = None) -> str:
    """Ask the question and return the chosen option's key.

    Ctrl+C raises KeyboardInterrupt, as the typed prompt does, so the caller's "nothing was
    committed" handling is unchanged.
    """
    console = console or Console()
    if not _can_draw():
        return typed(question, options, default, console)
    try:
        app = build(question, options, default, console=console)
    except Exception:                          # no usable console (or no prompt_toolkit)
        if os.environ.get("ZEROTRACE_DEBUG"):
            raise
        return typed(question, options, default, console)
    console.file.flush()
    _flush_typeahead()
    key = app.run()
    label = next(option.label for option in options if option.key == key)
    console.print(f"  [bold]{key.upper()}[/] {escape(label)}")
    return key


def preview(question: str, options: Sequence[Option], console: Console) -> Text:
    """How the menu looks before a key is pressed, for `zerotrace ui` (which never asks)."""
    pointer = _pointer(console)
    text = Text(f"{question}\n", style="bold")
    for index, option in enumerate(options):
        style = "reverse" if index == 0 else ""
        text.append(f"  {pointer if index == 0 else ' '} ", style=style)
        text.append(option.key.upper(), style=f"{style} bold".strip())
        text.append(f"  {option.label}  ", style=style)
        text.append("\n")
    text.append(f"  {_hint(console)}", style="dim")
    return text

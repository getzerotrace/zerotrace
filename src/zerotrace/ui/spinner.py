"""A themed "still working" line for the scans that run inside `git commit` and `git push`.

Reading the staged diff, running every detector and - for an ambiguous value - waiting on the
local model can take a minute or two on a laptop CPU. A terminal that prints nothing for that
long looks like a hung `git commit`, so while a scan runs one line says that ZeroTrace is at
work, on what, and for how long:

  zerotrace: ⠹ Lollygagging… scanning 11 staged files · 4s

  * rich's `Live` redraws it on stderr, which also keeps a warning printed mid-scan above the
    line instead of glued to the end of it,
  * it is erased when the scan ends (`transient`), so what is printed next is unchanged,
  * nothing is drawn - no thread, no escape sequence - unless stderr is a terminal that can
    move its cursor: an IDE, a pipe, CI and TERM=dumb get exactly the output they had before,
  * the glyph pulses through the same teal-to-emerald ramp as the install bar, the word is the
    accent colour (`ui/theme.py`), and a console that cannot encode braille gets `| / - \\`.
"""
import secrets
import time
from collections.abc import Callable, Iterator

from rich.console import Console, ConsoleOptions
from rich.live import Live
from rich.text import Text

from . import glyphs, theme

# Real English, chosen to be absurd: the point of the line is that you can see it move.
WORDS = (
    "Lollygagging", "Perambulating", "Ratiocinating", "Cogitating", "Excogitating",
    "Cerebrating", "Ruminating", "Percolating", "Flibbertigibbeting", "Discombobulating",
    "Reconnoitering", "Scrutinizing", "Rummaging", "Winnowing", "Ferreting", "Sleuthing",
    "Adjudicating", "Cross-examining", "Interrogating", "Peregrinating", "Tergiversating",
    "Shilly-shallying", "Dillydallying", "Woolgathering", "Gallivanting", "Meandering",
    "Pontificating", "Circumnavigating", "Prognosticating", "Extrapolating", "Deliberating",
    "Fine-tooth-combing", "Spelunking", "Assaying", "Sifting", "Untangling", "Deciphering",
    "Bloviating", "Vacillating", "Hobnobbing", "Perusing",
)

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ASCII = "|/-\\"
_FPS = 10
_WORD_SECONDS = 2.4


def _glow(tick: int) -> str:
    """The glyph's colour: teal to emerald and back again, one stop of the ramp per frame."""
    stops = theme.RAMP_RGB
    span = len(stops) - 1
    position = tick % (2 * span)
    red, green, blue = stops[position if position <= span else 2 * span - position]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _elapsed(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m {whole % 60:02d}s"


def render_line(console: Console, word: str, tick: int = 0, detail: str = "",
                seconds: float = 0.0) -> Text:
    """One frame. Pure: the spinner below and `zerotrace ui` both draw through it."""
    marks = glyphs.for_console(console)
    frames = _BRAILLE if glyphs.encodable(console, _BRAILLE) else _ASCII
    line = Text(no_wrap=True, overflow="ellipsis")
    line.append("zerotrace: ", style="dim")
    line.append(frames[tick % len(frames)], style=_glow(tick))
    line.append(f" {word}{marks['ellipsis']}", style=theme.ACCENT_BOLD)
    notes = [detail] if detail else []
    if seconds >= 1:
        notes.append(_elapsed(seconds))
    if notes:
        separator = f" {marks['dot']} "
        line.append(f"  {separator.join(notes)}", style="dim")
    return line


class _Line:
    """What `Live` redraws. Recomputed from the clock on every refresh, so it needs no state
    of its own beyond the note the caller may change."""

    def __init__(self, detail: str, clock: Callable[[], float]) -> None:
        self.detail = detail
        self._clock = clock
        self._started = clock()
        self._first = secrets.randbelow(len(WORDS))       # a different word first, every run

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> Iterator[Text]:
        seconds = self._clock() - self._started
        word = WORDS[(self._first + int(seconds / _WORD_SECONDS)) % len(WORDS)]
        yield render_line(console, word, int(seconds * _FPS), self.detail, seconds)


class Spinner:
    """`with Spinner("reading the staged diff") as spin:` ... `spin.detail("scanning 3 files")`.

    Does nothing at all when the console is not an interactive terminal.
    """

    def __init__(self, detail: str = "", *, console: Console | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.console = console or Console(stderr=True)
        self._line = _Line(detail, clock)
        self._live: Live | None = None

    def __enter__(self) -> "Spinner":
        if self.console.is_terminal and not self.console.is_dumb_terminal:
            self._live = Live(self._line, console=self.console, refresh_per_second=_FPS,
                              transient=True)
            self._live.start(refresh=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def detail(self, text: str) -> None:
        """Change what is being scanned; the next frame shows it."""
        self._line.detail = text

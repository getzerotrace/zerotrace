"""One progress bar, pinned to the last line, with the log scrolling above it.

The shape is borrowed from the yeet installer (BTI/GIST `install.sh`), because it answers the
question a person actually has during an install — "how much of this is left" — rather than a
spinner's "something is happening":

  [████████████░░░░░░░░░░]  55%  Registering core.hooksPath

  * the bar goes LAST and every log line is written by erasing it, printing, and drawing it
    again; a bar printed above its own log is pushed off the screen by the next line,
  * the accent gradient (`ui/theme.py`) runs left to right across the fill, one escape per
    colour stop rather than one per cell,
  * the label sits in a FIXED field, because a field that sizes to its contents makes the bar
    itself twitch while the percentage stands still, which reads as the percentage being wrong,
  * under 60 columns the label is dropped rather than squeezed,
  * no terminal (a pipe, CI, a redirected log) means plain "[n/total] label" lines instead:
    carriage returns in a log file are noise.
"""
import shutil
import sys
from typing import IO

from rich.console import Console

from . import capability, theme

_CHROME = 11          # "  [" + "] " + "100%" + a column of slack so a full bar never wraps
_LABEL_WIDTH = 28
_MIN_BAR = 10
_MIN_COLS_FOR_LABEL = 60
_RESET, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"


class Bar:
    """Call .step(label) once per completed unit of work, .log(line) to print above the bar,
    then .finish()."""

    def __init__(self, total: int, file: IO[str] | None = None, console: Console | None = None):
        self.total = max(total, 1)
        self.done = 0
        self.file = file or sys.stdout
        self.console = console or Console(file=self.file)
        self.tty = bool(getattr(self.file, "isatty", lambda: False)())
        self.unicode = self.tty and capability.supports_unicode(self.console)
        self.colour = self.tty and capability.decorations_allowed() \
            and self.console.color_system is not None
        self.full, self.empty = ("█", "░") if self.unicode else ("#", "-")
        self.label = ""
        self._drawn = False

    # --- geometry -----------------------------------------------------------------------
    def _columns(self) -> int:
        """Measured every frame from the real window, so a resize mid-install is picked up."""
        return shutil.get_terminal_size(fallback=(80, 24)).columns

    def _layout(self) -> tuple[int, int]:
        """(bar width, label field width) for the current window."""
        cols = self._columns()
        room = cols - _CHROME
        label_width = 0
        if cols >= _MIN_COLS_FOR_LABEL:
            label_width = min(_LABEL_WIDTH, cols // 3)
        width = room - label_width - 2
        if width < _MIN_BAR:
            label_width, width = 0, room
        return max(width, _MIN_BAR), label_width

    # --- drawing ------------------------------------------------------------------------
    def _ramp(self) -> list[str]:
        if not self.colour:
            return [""] * len(theme.RAMP_RGB)
        return theme.escapes(self.console)

    def _fill(self, width: int, filled: int) -> str:
        """The gradient, one escape per colour stop rather than one per cell."""
        ramp = self._ramp()
        out, done = [], 0
        for index, escape in enumerate(ramp):
            end = min((index + 1) * width // len(ramp), filled)
            if end > done:
                out.append(escape)
                out.append(self.full * (end - done))
                done = end
        if done < filled:                      # rounding leaves a few cells at 100%
            out.append(self.full * (filled - done))
            done = filled
        if done < width:
            out.append(_DIM if self.colour else "")
            out.append(self.empty * (width - done))
        return "".join(out)

    def _percent(self) -> int:
        return min(100, int(self.done * 100 / self.total))

    def draw(self) -> None:
        if not self.tty:
            return
        width, label_width = self._layout()
        percent = self._percent()
        bar = self._fill(width, percent * width // 100)
        dim, reset, bold = (_DIM, _RESET, _BOLD) if self.colour else ("", "", "")
        line = f"\r  {dim}[{reset}{bar}{reset}{dim}]{reset} {bold}{percent:3d}%{reset}"
        if label_width:
            line += f"  {dim}{self.label[:label_width]}{reset}"
        self.file.write(line + "\033[K")
        self.file.flush()
        self._drawn = True

    def clear(self) -> None:
        if self._drawn:
            self.file.write("\r\033[K")
            self.file.flush()
            self._drawn = False

    # --- the API the CLI uses -------------------------------------------------------------
    def step(self, label: str = "") -> None:
        self.done = min(self.done + 1, self.total)
        self.label = label
        if self.tty:
            self.draw()
        else:
            self.file.write(f"zerotrace: [{self.done}/{self.total}] {label}\n")
            self.file.flush()

    def log(self, line: str) -> None:
        """Print above the bar: erase it, write the line, draw it again."""
        self.clear()
        self.file.write(line + "\n")
        self.file.flush()
        self.draw()

    def finish(self) -> None:
        """Leave a finished bar on screen: 100% is the receipt that it completed."""
        if not self.tty or not self._drawn:
            return
        self.done = self.total
        self.draw()
        self.file.write("\n")
        self.file.flush()
        self._drawn = False

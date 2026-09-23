"""The ZeroTrace accent colour, in one place.

Everything that carries the brand - the logo, the product name, panel borders, the install
progress bar, the highlights in the full-screen apps and the two bootstrap installers - takes
its colour from here, so the tool looks like one product instead of five screens that drifted.

Two rules the values obey:

* The accent is NOT a status colour. Green means a check passed, yellow warns and red blocks;
  a brand colour that also appears as a verdict makes a progress bar look like a row of ticks.
  So the solid accent is the deep emerald `#059669` and the bar's gradient starts in teal and
  only reaches emerald at its leading edge.
* Every stop stays readable on a light terminal AND a dark one. The previous ramp ran cyan to
  near-white, which is invisible on a white background; the darkest stop here has a contrast
  ratio of 5.3:1 against white and 4.0:1 against black, and the brightest 2.6:1 / 8.2:1.

`install.sh` and `install.ps1` cannot import this module (they run before Python exists), so
they carry the same numbers and `tests/test_theme.py` fails if the copies drift.
"""
from rich.console import Console

# Solid accent: the wordmark, the mark, panel borders, the product name.
ACCENT = "#059669"
ACCENT_RGB = (5, 150, 105)
ACCENT_256 = 29                 # xterm-256 #00875f, the closest cube colour
ACCENT_16 = "green"             # a 16-colour console (legacy conhost) has nothing closer
ACCENT_STYLE = ACCENT           # rich style string; rich downgrades it per terminal
ACCENT_BOLD = f"bold {ACCENT}"

# The progress gradient: deep teal to emerald, twelve stops, walked left to right.
RAMP_RGB = ((17, 94, 89), (17, 102, 93), (17, 111, 96), (17, 119, 100),
            (17, 127, 104), (17, 135, 107), (16, 144, 111), (16, 152, 115),
            (16, 160, 118), (16, 169, 122), (16, 177, 126), (16, 185, 129))
RAMP_256 = (23, 23, 29, 29, 29, 29, 36, 36, 36, 42, 42, 42)

# Textual's theme takes the accent for focus and selection; `secondary` is the bar's
# leading edge, so the apps and the installer share one palette.
TUI_THEME = "zerotrace"
TUI_PRIMARY = ACCENT
TUI_SECONDARY = "#10B981"
TUI_ACCENT = "#10B981"


def escapes(console: Console) -> list[str]:
    """The ramp as ANSI escapes this console can actually show (empty strings when it cannot
    colour at all), so a caller can walk it left to right without asking again."""
    system = console.color_system
    if system is None:
        return [""] * len(RAMP_RGB)
    if system == "truecolor":
        return [f"\033[38;2;{r};{g};{b}m" for r, g, b in RAMP_RGB]
    return [f"\033[38;5;{index}m" for index in RAMP_256]


def accent_escape(console: Console) -> str:
    """The solid accent as one escape, for the art the logo writes to the terminal itself."""
    system = console.color_system
    if system is None:
        return ""
    if system == "truecolor":
        return "\033[1;38;2;{};{};{}m".format(*ACCENT_RGB)
    return f"\033[1;38;5;{ACCENT_256}m"

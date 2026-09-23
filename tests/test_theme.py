"""The brand palette's rules, which are invisible in a diff and easy to break by eye.

The previous ramp (cyan to near-white) looked fine on the machine it was written on and
disappeared on a light terminal. These are the properties that stop that happening again.
"""
import io

from rich.console import Console

from zerotrace.ui import theme


def _luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance."""
    channels = []
    for value in rgb:
        srgb = value / 255
        channels.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(rgb: tuple[int, int, int], other: tuple[int, int, int]) -> float:
    first, second = sorted((_luminance(rgb), _luminance(other)), reverse=True)
    return (first + 0.05) / (second + 0.05)


_WHITE, _BLACK = (255, 255, 255), (0, 0, 0)


def test_the_solid_accent_is_readable_on_a_light_and_a_dark_terminal():
    """It carries the product name and the panel borders, so it has to be text-legible on
    both. 3:1 is the WCAG floor for large text and UI components."""
    assert _contrast(theme.ACCENT_RGB, _WHITE) >= 3.0
    assert _contrast(theme.ACCENT_RGB, _BLACK) >= 3.0


def test_every_ramp_stop_stays_visible_on_both_backgrounds():
    """A bar is not text, but a stop that vanishes into the page is a bar that looks broken."""
    for stop in theme.RAMP_RGB:
        assert _contrast(stop, _WHITE) >= 2.5, stop
        assert _contrast(stop, _BLACK) >= 2.5, stop


def test_the_ramp_only_ever_gets_brighter():
    """Left to right, or the fill reads as two bars rather than one gradient."""
    levels = [_luminance(stop) for stop in theme.RAMP_RGB]
    assert levels == sorted(levels)
    assert len(theme.RAMP_RGB) == len(theme.RAMP_256)


def test_the_accent_is_not_one_of_the_status_colours():
    """Green passes, yellow warns, red blocks. A brand colour that reads as a verdict makes a
    filling progress bar look like a row of ticks, so the accent sits away from all three."""
    for status in ((0, 128, 0), (0, 255, 0), (255, 255, 0), (255, 0, 0)):
        distance = sum((a - b) ** 2 for a, b in zip(theme.ACCENT_RGB, status, strict=True)) ** 0.5
        assert distance > 90, f"the accent is too close to {status}"


def _console(color: str | None) -> Console:
    return Console(file=io.StringIO(), force_terminal=True, color_system=color)


def test_escapes_follow_what_the_console_can_show():
    truecolor = theme.escapes(_console("truecolor"))
    assert truecolor[0].startswith("\033[38;2;")
    indexed = theme.escapes(_console("256"))
    assert indexed[0] == f"\033[38;5;{theme.RAMP_256[0]}m"
    assert theme.escapes(_console(None)) == [""] * len(theme.RAMP_RGB)
    assert theme.accent_escape(_console(None)) == ""
    assert str(theme.ACCENT_256) in theme.accent_escape(_console("256"))

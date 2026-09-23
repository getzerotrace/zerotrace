"""Shared terminal-capability detection for every tiered renderer (logo, progress bar, …)
so they all agree on what this terminal can show.

Image support is the fiddly one: there is no query that works everywhere, so it is detected
from the terminal's own environment variables. `zerotrace ui` prints what was detected and
why, because "the logo is blocks, not the picture" is otherwise unexplainable.
"""
import os

from rich.console import Console

# Terminals known to render inline images, and the protocol each speaks. Kept to the ones
# where support is stable: an escape sent to a terminal that does not understand it prints
# as a screenful of garbage, which is worse than falling back to the card rendering.
_KITTY_PROTOCOL = ("wezterm", "ghostty")
_ITERM_PROTOCOL = ("iterm.app", "konsole")


def _term_program() -> str:
    return (os.environ.get("TERM_PROGRAM") or "").lower()


def image_protocol() -> str | None:
    """"kitty" | "iterm2" | None — which inline-image protocol this terminal speaks."""
    term = (os.environ.get("TERM") or "").lower()
    program = _term_program()
    lc_terminal = (os.environ.get("LC_TERMINAL") or "").lower()

    # Multiplexers swallow the escapes unless passthrough is configured, and a half-written
    # image escape corrupts the pane. Never send one through tmux/screen.
    if os.environ.get("TMUX") or term.startswith("screen"):
        return None

    if os.environ.get("KITTY_WINDOW_ID") or "kitty" in term or "ghostty" in term:
        return "kitty"
    if any(name in program for name in _KITTY_PROTOCOL):
        return "kitty"
    if lc_terminal == "iterm2" or os.environ.get("ITERM_SESSION_ID"):
        return "iterm2"
    if any(name in program for name in _ITERM_PROTOCOL):
        return "iterm2"
    return None


def is_kitty() -> bool:
    return image_protocol() == "kitty"


def is_iterm2() -> bool:
    return image_protocol() == "iterm2"


def decorations_allowed() -> bool:
    return not os.environ.get("CI") and not os.environ.get("NO_COLOR")


def supports_image(console: Console) -> bool:
    return bool(console.is_terminal and decorations_allowed() and image_protocol())


def supports_unicode(console: Console) -> bool:
    if not console.is_terminal or not decorations_allowed() or console.color_system is None:
        return False
    return "utf" in (console.encoding or "").lower()


def why_no_image(console: Console) -> str:
    """One line explaining the image tier's decision, for `zerotrace ui` and doctor."""
    if not console.is_terminal:
        return "output is redirected (not a terminal)"
    if os.environ.get("CI"):
        return "CI is set"
    if os.environ.get("NO_COLOR"):
        return "NO_COLOR is set"
    if os.environ.get("TMUX") or (os.environ.get("TERM") or "").startswith("screen"):
        return "running inside tmux/screen, which would corrupt the image escape"
    protocol = image_protocol()
    if protocol:
        return f"supported ({protocol} protocol)"
    program = _term_program() or os.environ.get("TERM") or "unknown"
    return (f"this terminal ({program}) is not known to render inline images; "
            "iTerm2, Kitty, WezTerm, Ghostty and Konsole do - the card rendering is used instead")

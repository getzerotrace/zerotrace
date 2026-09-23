"""Glyphs chosen for what the destination console can actually encode.

A legacy Windows console (cp437/cp1252), a stripped locale, or a redirected stream cannot
encode "✓", "·" or "…". `cli.main` already reconfigures stdout with errors="replace" so
nothing crashes, but replacement prints as "?" and reads like a bug. Pick characters the
stream can render instead, and keep the meaning.
"""
from rich.console import Console

_PROBE = "✓✗·…×—█"
_UNICODE = {"ok": "✓", "warn": "!", "fail": "✗", "dot": "·", "ellipsis": "…", "arrow": "→",
            "dash": "—", "bar": "█"}
_ASCII = {"ok": "+", "warn": "!", "fail": "x", "dot": "-", "ellipsis": "...", "arrow": "->",
          "dash": "-", "bar": "|"}


def encodable(console: Console, sample: str = _PROBE) -> bool:
    encoding = console.encoding or "utf-8"
    try:
        sample.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def for_console(console: Console) -> dict[str, str]:
    """The glyph set this console can print, richest first."""
    return dict(_UNICODE) if encodable(console) else dict(_ASCII)


def box_for(console: Console):
    """Rich's box style this console can encode: ASCII on cp437/cp1252/ascii streams."""
    from rich import box
    return box.ROUNDED if encodable(console, "╭─╮│╰╯┏┃━") else box.ASCII


def sanitize(text: str, console: Console) -> str:
    """Replace the glyphs this console lacks, leaving everything else untouched."""
    if encodable(console):
        return text
    for key, rich in _UNICODE.items():
        text = text.replace(rich, _ASCII[key])
    encoding = console.encoding or "ascii"
    return text.encode(encoding, "replace").decode(encoding, "replace")

"""Logo rendering: the mark on the left, the ZEROTRACE wordmark to its right.

Tiers, richest-supported-first:
  1. png     inline image via the Kitty or iTerm2 graphics protocol
  2. card    the mark as a dark silhouette on a vertical emerald-gradient card, using a
             terminal cell's two colours (fg + bg) per half block - so the cut-outs that make
             the face readable show the gradient instead of becoming holes in a blob
  3. unicode monochrome half-block silhouette, for a UTF-8 terminal without truecolour
  4. ascii   density-ramp art; safe on cp437/cp1252 consoles
  5. text    just the wordmark lines, when the terminal is too narrow for any mark

Auto-detected, or forced with ZEROTRACE_LOGO=png|card|unicode|ascii|text|off. CI and
NO_COLOR drop to ascii, and a redirected/non-tty destination never gets image escapes.

Assets are generated offline by scripts/render_logo_assets.py (needs Pillow); nothing
here imports an image library, because this runs on every commit.
"""
import base64
import os
import re
from importlib import resources

from rich.console import Console

from . import capability, theme

_PACKAGE = "zerotrace.ui.assets"
_ENV_VAR = "ZEROTRACE_LOGO"
_CHUNK = 4096            # bytes of base64 per Kitty graphics-protocol chunk
_GUTTER = "   "
_RESET = "\033[0m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# The card: a dark silhouette drawn ON a vertical emerald-gradient page. The background is the
# gradient (bright at the top, deepening toward the bottom, the same ramp as the wordmark); the
# ink is near-black. Painting the silhouette itself - not the page - keeps the cut-outs (eyes,
# beard, horn) showing the gradient, so the face stays readable instead of holes in a blob.
_MARK_INK = (6, 18, 14)
_WORDMARK = "ZEROTRACE"
_TAGLINE = ("secret & PII guardrail", "commits · AI agents · local-first",
            "no trace. no leaks. stays safe.")

# 7-row block letters with two-cell strokes: the wordmark at its proper weight, used
# whenever the terminal is wide enough to carry it.
_FONT_BIG = {
    "Z": ["███████", "     ██", "    ██ ", "   ██  ", "  ██   ", " ██    ", "███████"],
    "E": ["███████", "██     ", "██     ", "██████ ", "██     ", "██     ", "███████"],
    "R": ["██████ ", "██   ██", "██   ██", "██████ ", "██  ██ ", "██   ██", "██   ██"],
    "O": [" █████ ", "██   ██", "██   ██", "██   ██", "██   ██", "██   ██", " █████ "],
    "T": ["███████", "   ██  ", "   ██  ", "   ██  ", "   ██  ", "   ██  ", "   ██  "],
    "A": ["  ███  ", " ██ ██ ", "██   ██", "███████", "██   ██", "██   ██", "██   ██"],
    "C": [" █████ ", "██   ██", "██     ", "██     ", "██     ", "██   ██", " █████ "],
}

# 5-row fallback for terminals that cannot carry the big one.
_FONT = {
    "Z": ["█████", "   ██", "  ██ ", " ██  ", "█████"],
    "E": ["█████", "██   ", "████ ", "██   ", "█████"],
    "R": ["████ ", "██ ██", "████ ", "██ ██", "██ ██"],
    "O": ["█████", "██ ██", "██ ██", "██ ██", "█████"],
    "T": ["█████", "  ██ ", "  ██ ", "  ██ ", "  ██ "],
    "A": [" ███ ", "██ ██", "█████", "██ ██", "██ ██"],
    "C": ["█████", "██   ", "██   ", "██   ", "█████"],
}


def _forced_mode() -> str | None:
    return os.environ.get(_ENV_VAR, "").strip().lower() or None


def _read_bytes(name: str) -> bytes | None:
    try:
        return (resources.files(_PACKAGE) / name).read_bytes()
    except (FileNotFoundError, OSError):
        return None


def _read_text(name: str) -> str | None:
    data = _read_bytes(name)
    # Normalize CRLF -> LF: a Windows checkout (core.autocrlf) must not leak stray
    # \r into an escape/art payload written straight to the terminal.
    return data.decode("utf-8").replace("\r\n", "\n") if data is not None else None


def _kitty_escape(png: bytes) -> str:
    data = base64.b64encode(png).decode("ascii")
    chunks = [data[i:i + _CHUNK] for i in range(0, len(data), _CHUNK)] or [""]
    parts = []
    for i, chunk in enumerate(chunks):
        more = 0 if i == len(chunks) - 1 else 1
        control = f"a=T,f=100,m={more}" if i == 0 else f"m={more}"
        parts.append(f"\033_G{control};{chunk}\033\\")
    return "".join(parts)


def _iterm2_escape(png: bytes) -> str:
    data = base64.b64encode(png).decode("ascii")
    return f"\033]1337;File=inline=1;width=40;preserveAspectRatio=1;size={len(png)}:{data}\a"


def _block_wordmark(big: bool = False) -> list[str]:
    font, rows, gap = (_FONT_BIG, 7, " ") if big else (_FONT, 5, "  ")
    return [gap.join(font[ch][row] for ch in _WORDMARK) for row in range(rows)]


_INVERT = str.maketrans({"▀": "▄", "▄": "▀", "█": " ", " ": "█"})
_CARD_MARGIN = 2        # columns of card on each side of the mark


def _centred(mark: list[str], margin: int = _CARD_MARGIN) -> list[str]:
    """Crop to the columns the mark actually occupies, then pad both sides equally.

    The asset keeps the source image's bounding box, which is not symmetric around the
    silhouette (the artwork's outline sits inside it), so padding the raw lines leaves more
    card on one side than the other.
    """
    width = max((len(line) for line in mark), default=0)
    padded = [line.ljust(width) for line in mark]
    columns = [x for line in padded for x, char in enumerate(line) if char != " "]
    if not columns:
        return padded
    first, last = min(columns), max(columns)
    gutter = " " * margin
    return [gutter + line[first:last + 1] + gutter for line in padded]


def _inverted_mask(mark: list[str]) -> list[str]:
    """The mask with mark and page swapped: a filled card with the silhouette cut out of it.

    Without colour this is the only way to show the artwork as designed. Painting the
    silhouette itself white turns the face into a blob, because the cut-outs that carry the
    eyes, nose and beard become holes in that blob instead of light on a dark shape.
    """
    return [line.translate(_INVERT) for line in _centred(mark)]


def _logo_256_picks(rows: int) -> list[int]:
    """One 256-colour index per row, walked top-to-bottom across the logo gradient."""
    span = len(theme.LOGO_256) - 1
    if rows <= 1:
        return [theme.LOGO_256[0]]
    return [theme.LOGO_256[round(row / (rows - 1) * span)] for row in range(rows)]


def _card_lines(mark: list[str], console: Console) -> list[str]:
    """Paint the silhouette mask as the artwork: a dark mark on an emerald-gradient card.

    The mask already says which half of each cell is the mark ("▀" top, "▄" bottom, "█"
    both), so printing it with a near-black foreground on the gradient background *is* the
    logo - each row takes its own stop of the vertical gradient for its page, and the
    cut-outs that make the face readable show that gradient instead of becoming holes.
    Colour is applied here rather than baked into the asset, so 256-colour terminals get it
    too and NO_COLOR can still turn it off.
    """
    lines = _centred(mark)
    if not lines:
        return []
    if console.color_system == "truecolor":
        ramp = theme.logo_ramp(len(lines))
        return [f"\033[38;2;{_MARK_INK[0]};{_MARK_INK[1]};{_MARK_INK[2]};"
                f"48;2;{r};{g};{b}m{line}{_RESET}"
                for line, (r, g, b) in zip(lines, ramp, strict=True)]
    picks = _logo_256_picks(len(lines))
    return [f"\033[38;5;16;48;5;{idx}m{line}{_RESET}"
            for line, idx in zip(lines, picks, strict=True)]


def _ascii_safe(text: str) -> str:
    """cp437/cp1252 consoles cannot print "·"; never send them a character they lack."""
    return text.replace("·", "-").encode("ascii", "replace").decode("ascii")


def _text_lines(width: int, ascii_only: bool = False) -> list[str]:
    tagline = [_ascii_safe(line) if ascii_only else line for line in _TAGLINE]
    return [_WORDMARK, "", *[line for line in tagline if len(line) <= width]]


def _supports_card(console: Console) -> bool:
    """Any colour terminal can paint a card; only the palette differs."""
    return capability.supports_unicode(console) and console.color_system is not None


def _visible_width(line: str) -> int:
    return len(_ANSI_RE.sub("", line))


def _mark_lines(name: str) -> list[str]:
    art = _read_text(name)
    if art is None:
        return []
    lines = [line.rstrip("\n") for line in art.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _compose(mark: list[str], right: list[str]) -> list[str]:
    """Mark on the left, right-hand block vertically centred against it."""
    if not mark:
        return right
    mark_width = max((_visible_width(line) for line in mark), default=0)
    height = max(len(mark), len(right))
    top_mark = (height - len(mark)) // 2
    top_right = (height - len(right)) // 2

    out = []
    for i in range(height):
        left = mark[i - top_mark] if top_mark <= i < top_mark + len(mark) else ""
        text = right[i - top_right] if top_right <= i < top_right + len(right) else ""
        pad = " " * max(0, mark_width - _visible_width(left))
        out.append((left + pad + _GUTTER + text).rstrip())
    return out


def _paint(lines: list[str], console: Console) -> str:
    """Paint the art as a vertical emerald gradient, top-to-bottom.

    A line that already carries its own colour (the card paints itself dark-on-light) is left
    untouched; everything else takes the gradient stop for its row, so the mark and the
    wordmark beside it catch the light on the same rows instead of one flat fill.
    """
    body = "\n".join(lines)
    if console.color_system is None or not capability.decorations_allowed():
        return body + "\n"
    escapes = theme.logo_escapes(console, len(lines))
    painted = [line if "\033[" in line else f"{escape}{line}{_RESET}"
               for escape, line in zip(escapes, lines, strict=True)]
    return "\n".join(painted) + "\n"


def _gradient_lines(lines: list[str], console: Console) -> list[str]:
    """Paint each line with its own stop of the vertical emerald gradient.

    Used for the card tier's wordmark, so the name deepens top-to-bottom just like the mark
    beside it. `_compose` joins the mark and the wordmark into one string, and the card's mark
    already carries its own escapes - so painting the name here keeps it from coming out in the
    terminal's default white when `_paint` skips the "already coloured" joined line.
    """
    escapes = theme.logo_escapes(console, len(lines))
    return [f"{esc}{line}{_RESET}" if (esc and line) else line
            for esc, line in zip(escapes, lines, strict=True)]


def _composition(console: Console, unicode_tier: bool, card: bool = False) -> list[str]:
    """Widest layout the terminal can take: big mark + block wordmark, down to text only."""
    width = console.width or 80
    suffix = "uni.txt" if (unicode_tier or card) else "ascii.txt"
    big = _mark_lines(f"mark.{suffix}")
    small = _mark_lines(f"mark.small.{suffix}")
    if card:
        big, small = _card_lines(big, console), _card_lines(small, console)
    elif unicode_tier:
        big, small = _inverted_mask(big), _inverted_mask(small)
    big_width = max((_visible_width(line) for line in big), default=0)
    small_width = max((_visible_width(line) for line in small), default=0)
    wide = _block_wordmark(big=True)
    wide_width = max(len(line) for line in wide)
    block = _block_wordmark()
    block_width = max(len(line) for line in block)

    ascii_only = not unicode_tier and not card
    # Only the card carries its own colour; everything else is painted once by `_paint`.
    def right(lines: list[str]) -> list[str]:
        return _gradient_lines(lines, console) if card else lines

    if big and width >= big_width + len(_GUTTER) + wide_width:
        return _compose(big, right(wide + ["", _TAGLINE[-1]]))
    if big and (unicode_tier or card) and width >= big_width + len(_GUTTER) + block_width:
        return _compose(big, right(block + ["", _TAGLINE[-1]]))
    if big and width >= big_width + len(_GUTTER) + 24:
        return _compose(big, right(_text_lines(width - big_width - len(_GUTTER), ascii_only)))
    if small and width >= small_width + len(_GUTTER) + 24:
        return _compose(small, right(_text_lines(width - small_width - len(_GUTTER), ascii_only)))
    if small and width >= small_width:
        return small + ["", *right(_text_lines(width, ascii_only))]
    return right(_text_lines(width, ascii_only))


def render(console: Console) -> str | None:
    """The richest logo rendering this terminal can show, or None to print nothing."""
    forced = _forced_mode()
    if forced == "off":
        return None

    if forced == "png" or (forced is None and capability.supports_image(console)):
        png = _read_bytes("logo.png")
        if png is not None:
            return _kitty_escape(png) if capability.is_kitty() else _iterm2_escape(png)

    if forced == "text":
        return _paint(_text_lines(console.width or 80, not capability.supports_unicode(console)),
                      console)

    if forced == "card" or (forced is None and _supports_card(console)):
        lines = _composition(console, unicode_tier=True, card=True)
        if any("\033[" in line for line in lines):
            return _paint(lines, console)

    unicode_tier = forced == "unicode" or (forced is None and capability.supports_unicode(console))
    return _paint(_composition(console, unicode_tier), console)

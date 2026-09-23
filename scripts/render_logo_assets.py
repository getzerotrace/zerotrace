"""Regenerate the terminal-logo assets from the source image.

    python scripts/render_logo_assets.py [path/to/logo.png] [--width 30]

Writes into src/zerotrace/ui/assets/:
  logo.png        optimized copy, sent as-is via the Kitty/iTerm2 image protocols
  mark.uni.txt    the mark in Unicode half-blocks (▀▄█), two samples per cell
  mark.ascii.txt  the same mark as an ASCII density ramp (" .:-=+*#%@")
  mark.small.*    the same two at a narrower width, for 60-column terminals

Coverage is supersampled and averaged, so edges and the white cut-outs survive as
mid-tones; a hard black/white threshold loses the eyes, mouth and horn detail.

The source art is a black silhouette with white cut-out details on a transparent
background, so "ink" is *opaque and dark*: the silhouette becomes white blocks in the
terminal and the cut-outs stay empty. Colour is applied by the renderer (a single white
style), never baked in here, so NO_COLOR and redirected output still behave.

Needs Pillow (a dev extra). The runtime package never imports it: this is a maintainer
step, and the generated assets are committed.
"""
import sys
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "src" / "zerotrace" / "ui" / "assets"
DEFAULT_SOURCE = ASSETS / "logo2.png"

_PNG_MAX_SIDE = 240
_CELL_ASPECT = 2.0   # a terminal cell is about twice as tall as it is wide
_OPAQUE = 128        # alpha at or above this counts as part of the artwork
_DARK = 140          # luminance below this is the silhouette
_LIGHT = 150         # luminance at or above this is the outline and the cut-out detail

_SHADE_RAMP = " ░▒▓█"            # lightest -> darkest; all four exist in CP437
_ASCII_RAMP = " .:-=+*#%@"       # the classic density ramp


def coverage(img: Image.Image, width: int, supersample: int = 4,
             rows_per_cell: int = 1, ink: str = "dark") -> list[list[float]]:
    """Per-cell ink coverage in 0..1, computed by supersampling then averaging.

    "Ink" is opaque *and* dark: the mark is a black silhouette whose white details are
    cut-outs, so averaging preserves the eyes, mouth and horn edges as mid-tones instead
    of collapsing them to a hard threshold (which is what made the first pass look wrong).
    """
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    src_w, src_h = img.size
    rows = max(round(src_h * width / src_w / _CELL_ASPECT) * rows_per_cell, 1)

    fine = img.resize((width * supersample, rows * supersample), Image.LANCZOS)
    pixels = fine.load()

    grid = []
    for cell_y in range(rows):
        line = []
        for cell_x in range(width):
            hits = 0
            for dy in range(supersample):
                for dx in range(supersample):
                    r, g, b, a = pixels[cell_x * supersample + dx, cell_y * supersample + dy]
                    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
                    lit = luma >= _LIGHT if ink == "light" else luma < _DARK
                    hits += 1 if (a >= _OPAQUE and lit) else 0
            line.append(hits / (supersample * supersample))
        grid.append(line)
    return grid


def _ramp_art(grid: list[list[float]], ramp: str) -> str:
    """Map coverage to a character ramp (lightest first), trimming trailing blanks."""
    last = len(ramp) - 1
    lines = []
    for row in grid:
        line = "".join(ramp[min(last, int(value * len(ramp)))] for value in row)
        lines.append(line.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def to_halfblocks(img: Image.Image, width: int, cutoff: float = 0.45) -> str:
    """Crisp Unicode art: two vertical samples per cell via ▀ ▄ █.

    A shading ramp dithers edges and reads as noise at logo size; half-blocks double the
    vertical resolution and keep the silhouette sharp, which is what a mark needs.
    """
    grid = coverage(img, width, supersample=2, rows_per_cell=2)
    lines = []
    for y in range(0, len(grid), 2):
        top = grid[y]
        bottom = grid[y + 1] if y + 1 < len(grid) else [0.0] * len(top)
        line = "".join(
            "█" if t >= cutoff and b >= cutoff else
            "▀" if t >= cutoff else
            "▄" if b >= cutoff else " "
            for t, b in zip(top, bottom, strict=False)
        )
        lines.append(line.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def to_ascii(grid: list[list[float]]) -> str:
    """The OUTLINE, not the silhouette.

    A filled ASCII silhouette is a blob of `@`: at one character per cell there is no room
    for the cut-outs that make the face readable. Drawing the artwork's light parts instead
    -- the outline, horns, eyes, mouth, beard -- gives a line drawing that reads at 60-80
    columns, which is what the ASCII tier is for.
    """
    return _ramp_art(grid, _ASCII_RAMP)


_PAGE = (245, 245, 245)     # the card the mark is printed on
_MARK = (16, 16, 16)        # the silhouette itself


def to_card_ansi(img: Image.Image, width: int, cutoff: float = 0.5) -> str:
    """The logo as designed: dark silhouette on a light card, using fg+bg colour.

    A terminal cell can hold two colours (foreground and background), so "▀" paints the top
    half in the foreground colour and the bottom half in the background colour. That gives
    both the square pixels of a half-block render *and* the real artwork: the white cut-outs
    (eyes, mouth, beard) stay white instead of becoming holes in a white blob.
    """
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    src_w, src_h = img.size
    rows = max(round(src_h * width / src_w / _CELL_ASPECT) * 2, 2)
    small = img.resize((width, rows), Image.LANCZOS)
    pixels = small.load()

    def colour(x: int, y: int) -> tuple[int, int, int]:
        r, g, b, a = pixels[x, y]
        if a < _OPAQUE:
            return _PAGE                     # outside the artwork: still part of the card
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return _MARK if luma < 140 else _PAGE

    lines = []
    for y in range(0, rows, 2):
        parts: list[str] = []
        current: tuple | None = None          # emit an escape only when the pair changes
        for x in range(width):
            top = colour(x, y)
            bottom = colour(x, y + 1) if y + 1 < rows else _PAGE
            if (top, bottom) != current:
                parts.append(f"\033[38;2;{top[0]};{top[1]};{top[2]};"
                             f"48;2;{bottom[0]};{bottom[1]};{bottom[2]}m")
                current = (top, bottom)
            parts.append("▀")
        lines.append("".join(parts) + "\033[0m")
    return "\n".join(lines) + "\n"


def write_png(img: Image.Image) -> None:
    """Composite onto the same light card before shipping it.

    The source is a dark silhouette on transparency, so an inline image with its alpha intact
    is invisible on a dark terminal - the exact problem the card rendering solves for the text
    tiers.
    """
    art = img.convert("RGBA")
    bbox = art.getchannel("A").getbbox()
    if bbox:
        art = art.crop(bbox)
    art.thumbnail((_PNG_MAX_SIDE, _PNG_MAX_SIDE), Image.LANCZOS)
    margin = 12
    card = Image.new("RGBA", (art.width + margin * 2, art.height + margin * 2), (*_PAGE, 255))
    card.alpha_composite(art, (margin, margin))
    card.convert("RGB").save(ASSETS / "logo.png", optimize=True)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    width = 30
    for arg in argv:
        if arg.startswith("--width="):
            width = int(arg.split("=", 1)[1])
    source = Path(args[0]) if args else DEFAULT_SOURCE
    if not source.is_file():
        print(f"no such image: {source}", file=sys.stderr)
        return 2

    img = Image.open(source)
    for name, text in (("mark.uni.txt", to_halfblocks(img, width)),
                       ("mark.card.ans", to_card_ansi(img, width)),
                       ("mark.ascii.txt", to_ascii(coverage(img, width)))):
        (ASSETS / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {name}: {len(text.splitlines())} lines x {width} cols")
    write_png(img)
    print(f"wrote logo.png (max side {_PNG_MAX_SIDE}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

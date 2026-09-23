"""Logo tiers (png -> unicode -> ascii -> text), width fallbacks and colour rules."""
import io

import pytest
from rich.console import Console

from zerotrace.ui import logo, theme

_ENV_KEYS = ("ZEROTRACE_LOGO", "KITTY_WINDOW_ID", "TERM", "TERM_PROGRAM", "CI", "NO_COLOR")
_BLOCKS = set("░▒▓█")   # the shading ramp the unicode tier draws with


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # The real CI (this suite's own pipeline) sets CI=true, which would otherwise
    # leak into these tests and force every "auto mode" case into the ascii tier.
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _console(*, terminal: bool = True, color: str | None = "truecolor", width: int = 100) -> Console:
    return Console(file=io.StringIO(), force_terminal=terminal, color_system=color, width=width)


def _plain(art: str) -> str:
    """The art with every colour escape removed, whichever tier painted it."""
    return logo._ANSI_RE.sub("", art)


def _shows_the_name(art: str) -> bool:
    """The plain wordmark, or either block-letter version used on wider terminals."""
    return ("ZEROTRACE" in art
            or logo._block_wordmark()[0] in art
            or logo._block_wordmark(big=True)[0] in art)


# --- tiers --------------------------------------------------------------------------

def test_forced_off_prints_nothing():
    import os
    os.environ["ZEROTRACE_LOGO"] = "off"
    try:
        assert logo.render(_console()) is None
    finally:
        del os.environ["ZEROTRACE_LOGO"]


def test_image_tier_used_on_kitty(monkeypatch):
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    monkeypatch.setattr(logo, "_read_bytes", lambda name: b"png-bytes" if name == "logo.png" else None)
    assert logo.render(_console()).startswith("\033_G")


def test_image_tier_used_on_iterm2(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(logo, "_read_bytes", lambda name: b"png-bytes" if name == "logo.png" else None)
    assert "\033]1337;File=inline=1" in logo.render(_console())


def test_unicode_tier_draws_the_mark_and_the_wordmark(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    art = _plain(logo.render(_console(width=120)))
    assert _BLOCKS & set(art)                # the mark
    assert "█████" in art                    # the block wordmark at this width


def test_ascii_tier_is_pure_ascii(monkeypatch):
    """cp437 / cp1252 consoles must never receive a character they cannot encode."""
    monkeypatch.setenv("ZEROTRACE_LOGO", "ascii")
    art = _plain(logo.render(_console(width=100)))
    art.encode("cp437")                      # raises if any glyph is unrepresentable
    assert set("@%#*+=:-.") & set(art) and "ZEROTRACE" in art
    assert not _BLOCKS & set(art)


def test_no_terminal_falls_back_to_ascii():
    art = _plain(logo.render(_console(terminal=False)))
    art.encode("cp437")
    assert not _BLOCKS & set(art)


def test_ci_forces_ascii_even_on_a_capable_terminal(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    art = _plain(logo.render(_console()))
    assert not _BLOCKS & set(art)
    assert "\033_G" not in art


def test_no_color_disables_colour_and_unicode(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    art = logo.render(_console())
    assert "\033[" not in art
    assert not _BLOCKS & set(art)


def test_colour_is_applied_only_when_the_console_has_a_colour_system(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "ascii")
    colour = _console(color="truecolor")
    assert theme.accent_escape(colour) in logo.render(colour)
    assert "\033[" not in logo.render(_console(color=None))


def test_the_mark_and_the_name_are_drawn_in_the_brand_accent(monkeypatch):
    """One accent for the logo, the panels, the progress bar and the installers."""
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    art = logo.render(_console(color="truecolor"))
    assert "38;2;{};{};{}".format(*theme.ACCENT_RGB) in art
    assert "38;5;" not in art, "a truecolour terminal gets the exact accent, not an index"


# --- width fallbacks ------------------------------------------------------------------

@pytest.mark.parametrize("width", [200, 120, 100, 80, 60, 40, 30, 20, 10])
def test_every_width_renders_and_fits(width, monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    art = _plain(logo.render(_console(width=width)))
    assert _shows_the_name(art)
    longest = max(len(line) for line in art.splitlines())
    assert longest <= width, f"{longest} > {width}"


def test_narrow_terminal_drops_the_mark_but_keeps_the_name(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    art = _plain(logo.render(_console(width=18)))
    assert art.splitlines()[0] == "ZEROTRACE"
    assert not _BLOCKS & set(art)


def test_wordmark_sits_to_the_right_of_the_mark(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    lines = _plain(logo.render(_console(width=80))).splitlines()
    (row,) = [line for line in lines if "ZEROTRACE" in line]
    assert row.index("ZEROTRACE") > 20, "the wordmark must start after the mark, not above it"
    assert _BLOCKS & set(row[:20]), "the mark should occupy the left of that row"


# --- assets ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["mark.uni.txt", "mark.ascii.txt",
                                  "mark.small.uni.txt", "mark.small.ascii.txt"])
def test_generated_assets_ship_in_the_package(name):
    art = logo._read_text(name)
    assert art and art.strip(), name


def test_assets_have_no_trailing_whitespace_or_crlf():
    for name in ("mark.uni.txt", "mark.ascii.txt"):
        raw = logo._read_bytes(name).decode("utf-8")
        assert "\r" not in raw, name
        assert all(line == line.rstrip() for line in raw.splitlines()), name


def test_missing_assets_degrade_to_the_wordmark(monkeypatch):
    monkeypatch.setattr(logo, "_read_bytes", lambda name: None)
    art = _plain(logo.render(_console()))
    assert art.splitlines()[0] == "ZEROTRACE"


# --- the card rendering (the mark as designed: the accent on a light card) --------------

def test_card_tier_paints_a_light_background(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "card")
    art = logo.render(_console(width=120))
    assert "\033[48;2;245;245;245m" in art or "48;2;245;245;245m" in art
    assert "38;2;{};{};{}".format(*theme.ACCENT_RGB) in art, \
        "the silhouette itself is painted, so its cut-outs stay light"


def test_card_tier_falls_back_to_256_colour(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "card")
    art = logo.render(_console(width=120, color="256"))
    assert f"\033[38;5;{theme.ACCENT_256};48;5;255m" in art


def test_card_is_used_automatically_on_a_colour_terminal():
    art = logo.render(_console(width=120))
    assert "48;2;" in art, "a colour UTF-8 terminal should get the card, not the mono mark"


def test_no_color_drops_the_card(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    art = logo.render(_console(width=120))
    assert "48;2;" not in art


def test_wide_terminals_get_the_broad_wordmark(monkeypatch):
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    art = _plain(logo.render(_console(width=130)))
    assert logo._block_wordmark(big=True)[0] in art
    for glyph in "ZEROTRACE":
        assert glyph in logo._FONT_BIG


def test_ascii_tier_draws_the_outline_not_a_blob(monkeypatch):
    """A filled ASCII silhouette is unreadable; the outline is the point of this tier."""
    monkeypatch.setenv("ZEROTRACE_LOGO", "ascii")
    art = _plain(logo.render(_console(width=110)))
    lines = [line for line in art.splitlines() if line.strip()]
    densest = max(line.count("@") for line in lines)
    assert densest < 30, "the ASCII mark should be a line drawing, not a solid block"
    assert "@" in art and "." in art


def test_mono_tier_is_inverted_so_the_head_reads_as_the_artwork(monkeypatch):
    """Without colour the page is filled and the silhouette is cut out of it: painting the
    silhouette itself turns the face into a blob, because its white detail becomes holes."""
    monkeypatch.setenv("ZEROTRACE_LOGO", "unicode")
    lines = [line for line in _plain(logo.render(_console(width=130))).splitlines()
             if "█" in line]
    assert lines[0].startswith("█"), "the card is filled at the edges"
    assert any(" " in line[:46] for line in lines), "and the mark is cut out of it"
    # More page than mark, measured over the mark's own columns (the rest of the line is
    # the wordmark and the gutter).
    mark_columns = [line[:46] for line in lines]
    filled = sum(line.count("█") for line in mark_columns)
    empty = sum(line.count(" ") for line in mark_columns)
    assert filled > empty


def test_card_margins_are_equal_on_both_sides():
    """The asset keeps the source's off-centre bounding box; the card must not."""
    mark = logo._mark_lines("mark.uni.txt")
    centred = logo._centred(mark)
    left = min(len(line) - len(line.lstrip(" ")) for line in centred if line.strip())
    right = min(len(line) - len(line.rstrip(" ")) for line in centred if line.strip())
    assert left == right == logo._CARD_MARGIN
    assert len({len(line) for line in centred}) == 1, "the card is a rectangle"

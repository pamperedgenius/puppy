"""Font/glyph tests: real HarfBuzz shaping + real FreeType rasterization
against whatever monospace font fontconfig resolves on this system (no
hardcoded font path -- portable across machines). Skips gracefully if the
render deps aren't installed or no monospace font can be found at all.
"""
import shutil
import subprocess

import pytest

pytest.importorskip("freetype")
pytest.importorskip("uharfbuzz")

from puppy.render.font import FontRenderer


def _find_monospace_font() -> str | None:
    if not shutil.which("fc-match"):
        return None
    result = subprocess.run(
        ["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True
    )
    path = result.stdout.strip()
    return path or None


_FONT_PATH = _find_monospace_font()
pytestmark = pytest.mark.skipif(_FONT_PATH is None, reason="no monospace font found via fontconfig")


@pytest.fixture
def font():
    return FontRenderer(_FONT_PATH, pixel_size=16)


def test_cell_metrics_are_positive_integers(font):
    assert isinstance(font.cell_width, int) and font.cell_width > 0
    assert isinstance(font.cell_height, int) and font.cell_height > 0
    assert isinstance(font.ascender, int) and font.ascender > 0


def test_monospace_font_has_equal_advance_across_different_chars(font):
    # Confirmed real (not a script bug) by hand before writing this module:
    # a genuinely monospace font's hinted advance is identical across chars.
    ft_face = font._ft_face
    import freetype as ft

    advances = []
    for ch in "MiW.":
        ft_face.load_char(ch, ft.FT_LOAD_RENDER)
        advances.append(ft_face.glyph.advance.x)
    assert len(set(advances)) == 1


def test_glyph_id_for_space_and_letter_differ():
    font = FontRenderer(_FONT_PATH, pixel_size=16)
    space_id = font.glyph_id_for_char(" ")
    m_id = font.glyph_id_for_char("M")
    assert space_id != m_id


def test_glyph_id_is_consistent_for_same_char(font):
    assert font.glyph_id_for_char("A") == font.glyph_id_for_char("A")


def test_glyph_id_for_char_is_cached_not_reshaped_every_call(font):
    font.glyph_id_for_char("A")
    font.glyph_id_for_char("A")
    font.glyph_id_for_char("B")
    assert len(font._char_to_glyph_id) == 2


def test_rasterize_letter_produces_nonzero_pixel_data(font):
    bitmap = font.rasterize_char("M")
    assert bitmap.width > 0
    assert bitmap.height > 0
    assert any(p != 0 for p in bitmap.pixels)


def test_rasterize_space_produces_no_ink():
    font = FontRenderer(_FONT_PATH, pixel_size=16)
    bitmap = font.rasterize_char(" ")
    # A space glyph may have zero-size bitmap dimensions, or a bitmap that's
    # entirely zero coverage -- either is correct, just confirm it's not
    # accidentally rendering visible ink.
    assert bitmap.width == 0 or all(p == 0 for p in bitmap.pixels)


def test_rasterize_caches_by_glyph_id(font):
    glyph_id = font.glyph_id_for_char("M")
    first = font.rasterize(glyph_id)
    second = font.rasterize(glyph_id)
    assert first is second  # identity check: proves caching, not just equal data


def test_pixel_buffer_length_matches_width_times_height(font):
    # Regression guard for the pitch-vs-width bug: bitmap.buffer can be
    # pitch*rows bytes (padded), the stored pixels must be exactly
    # width*height with padding stripped.
    for ch in "MiW. #@":
        bitmap = font.rasterize_char(ch)
        assert len(bitmap.pixels) == bitmap.width * bitmap.height


def test_bold_produces_more_ink_than_regular(font):
    regular = font.rasterize_char("M", bold=False)
    bold = font.rasterize_char("M", bold=True)
    assert sum(bold.pixels) > sum(regular.pixels)


def test_bold_and_regular_are_cached_separately(font):
    regular = font.rasterize_char("M", bold=False)
    bold = font.rasterize_char("M", bold=True)
    assert regular is not bold
    # calling again returns the same cached objects, not fresh re-renders
    assert font.rasterize_char("M", bold=False) is regular
    assert font.rasterize_char("M", bold=True) is bold


def test_underline_metrics_are_sane(font):
    assert isinstance(font.underline_thickness, int) and font.underline_thickness >= 1
    # underline sits below the baseline, above the very bottom of the cell,
    # and within the cell bounds
    assert font.ascender < font.underline_y < font.cell_height

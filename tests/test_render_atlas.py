import numpy as np
import pytest

from puppy.render.atlas import GlyphAtlas
from puppy.render.font import GlyphBitmap


def _bitmap(width, height, fill_start=1):
    pixels = bytes(range(fill_start, fill_start + width * height))
    return GlyphBitmap(width=width, height=height, bearing_x=0, bearing_y=height, pixels=pixels)


def test_first_glyph_gets_slot_zero():
    atlas = GlyphAtlas(cell_width=8, cell_height=12, ascender=10)
    slot = atlas.get_or_add(65, _bitmap(4, 4))
    assert (slot.col, slot.row) == (0, 0)


def test_same_glyph_id_returns_cached_slot():
    atlas = GlyphAtlas(cell_width=8, cell_height=12, ascender=10)
    first = atlas.get_or_add(65, _bitmap(4, 4))
    second = atlas.get_or_add(65, _bitmap(4, 4))
    assert first is second


def test_different_glyphs_pack_sequentially():
    atlas = GlyphAtlas(cell_width=8, cell_height=12, ascender=10, cols=4)
    a = atlas.get_or_add(1, _bitmap(2, 2))
    b = atlas.get_or_add(2, _bitmap(2, 2))
    assert (a.col, a.row) == (0, 0)
    assert (b.col, b.row) == (1, 0)


def test_uv_rect_covers_the_full_cell_not_just_the_glyph_bitmap():
    # Confirmed design: every slot's UV rect is the full cell, glyphs are
    # baseline-positioned *within* it at blit time -- not a variable
    # per-glyph-sized UV rect.
    atlas = GlyphAtlas(cell_width=8, cell_height=12, ascender=10, cols=4)
    slot = atlas.get_or_add(1, _bitmap(2, 2))  # bitmap much smaller than cell
    assert slot.u1 - slot.u0 == pytest.approx(8 / atlas.width)
    assert slot.v1 - slot.v0 == pytest.approx(12 / atlas.height)


def test_glyph_is_baseline_positioned_within_its_slot():
    cell_width, cell_height, ascender = 4, 6, 4
    atlas = GlyphAtlas(cell_width=cell_width, cell_height=cell_height, ascender=ascender)
    bitmap = GlyphBitmap(width=2, height=2, bearing_x=1, bearing_y=2, pixels=bytes([10, 20, 30, 40]))
    atlas.get_or_add(1, bitmap)
    # off_x = bearing_x = 1, off_y = ascender - bearing_y = 4 - 2 = 2
    # -> lands at rows [2:4], cols [1:3] of the (only) cell slot at (0,0)
    region = atlas.image[2:4, 1:3]
    assert region.tolist() == [[10, 20], [30, 40]]
    # nothing outside that region should have been touched
    assert atlas.image.sum() == 10 + 20 + 30 + 40


def test_zero_size_glyph_leaves_slot_blank():
    atlas = GlyphAtlas(cell_width=8, cell_height=12, ascender=10)
    space = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    atlas.get_or_add(1, space)
    assert atlas.image.sum() == 0
    assert atlas.dirty_rect is None


def test_oversized_glyph_is_clipped_not_crashed_or_corrupting_neighbors():
    cell_width, cell_height, ascender = 4, 4, 3
    atlas = GlyphAtlas(cell_width=cell_width, cell_height=cell_height, ascender=ascender, cols=2)
    huge = GlyphBitmap(width=10, height=10, bearing_x=0, bearing_y=10, pixels=bytes([99] * 100))
    atlas.get_or_add(1, huge)  # must not raise
    second = atlas.get_or_add(2, _bitmap(2, 2, fill_start=1))
    # second glyph's slot (col=1) must be untouched by the first's overflow
    second_region = atlas.image[:, cell_width:cell_width * 2]
    assert (second_region == 99).sum() == 0


def test_dirty_rect_tracks_and_grows_then_clears():
    atlas = GlyphAtlas(cell_width=4, cell_height=4, ascender=3, cols=4)
    assert atlas.dirty_rect is None
    atlas.get_or_add(1, _bitmap(2, 2))
    first_dirty = atlas.dirty_rect
    assert first_dirty is not None
    atlas.get_or_add(2, _bitmap(2, 2))
    second_dirty = atlas.dirty_rect
    assert second_dirty is not None
    # the union should be at least as wide as covering both glyphs (col 0 and col 1)
    assert second_dirty[2] > first_dirty[2] or second_dirty[0] < first_dirty[0]
    atlas.clear_dirty()
    assert atlas.dirty_rect is None


def test_atlas_full_raises():
    atlas = GlyphAtlas(cell_width=2, cell_height=2, ascender=1, cols=1, rows=1)
    atlas.get_or_add(1, _bitmap(1, 1))
    with pytest.raises(RuntimeError):
        atlas.get_or_add(2, _bitmap(1, 1))

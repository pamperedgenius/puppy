"""Tests for the Screen -> instance-array wiring logic (build_instances) and
font resolution. No GPU/window needed here -- these are pure CPU-side tests
using a real Screen + real FontRenderer/GlyphAtlas.
"""
import shutil
import subprocess

import pytest

pytest.importorskip("freetype")
pytest.importorskip("uharfbuzz")

from puppy.render.app import build_instances, find_monospace_font
from puppy.render.atlas import GlyphAtlas
from puppy.render.cell_renderer import INSTANCE_DTYPE
from puppy.render.color import srgb_color
from puppy.render.font import FontRenderer
from puppy.screen import Screen


def _find_monospace_font():
    if not shutil.which("fc-match"):
        return None
    result = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True)
    return result.stdout.strip() or None


_FONT_PATH = _find_monospace_font()
pytestmark = pytest.mark.skipif(_FONT_PATH is None, reason="no monospace font found via fontconfig")


@pytest.fixture
def font():
    return FontRenderer(_FONT_PATH, pixel_size=16)


@pytest.fixture
def atlas(font):
    return GlyphAtlas(cell_width=font.cell_width, cell_height=font.cell_height, ascender=font.ascender)


def test_find_monospace_font_returns_real_path():
    path = find_monospace_font()
    assert path
    import os

    assert os.path.isfile(path)


def test_build_instances_produces_one_per_cell(font, atlas):
    screen = Screen(rows=3, cols=5)
    instances = build_instances(screen, font, atlas)
    assert instances.dtype == INSTANCE_DTYPE
    assert len(instances) == 15


def test_build_instances_positions_match_grid_coordinates(font, atlas):
    screen = Screen(rows=2, cols=3)
    instances = build_instances(screen, font, atlas)
    # row-major order: index = row*cols + col
    assert (instances[0]["col"], instances[0]["row"]) == (0, 0)
    assert (instances[1]["col"], instances[1]["row"]) == (1, 0)
    assert (instances[3]["col"], instances[3]["row"]) == (0, 1)


def test_build_instances_uses_default_colors_for_unset_cells(font, atlas):
    screen = Screen(rows=1, cols=1)
    instances = build_instances(screen, font, atlas)
    assert list(instances[0]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[0]["bg"]) == list(srgb_color(0, 0, 0))


def test_build_instances_applies_explicit_sgr_color(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.sgr([31])  # basic red foreground -> normalized index 1
    screen.put_char("x")
    instances = build_instances(screen, font, atlas)
    assert list(instances[0]["fg"]) == list(srgb_color(128, 0, 0))


def test_build_instances_swaps_fg_bg_on_reverse(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.sgr([31, 7])  # red fg + reverse
    screen.put_char("x")
    instances = build_instances(screen, font, atlas)
    # reversed: fg becomes the default bg color, bg becomes red
    assert list(instances[0]["fg"]) == list(srgb_color(0, 0, 0))
    assert list(instances[0]["bg"]) == list(srgb_color(128, 0, 0))


def test_build_instances_respects_osc4_palette_override(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.set_palette_color(1, "#00ff00")  # override "red" (index 1) to green
    screen.sgr([31])
    screen.put_char("x")
    instances = build_instances(screen, font, atlas)
    assert list(instances[0]["fg"]) == list(srgb_color(0, 255, 0))


def test_build_instances_reuses_atlas_slots_for_repeated_chars(font, atlas):
    screen = Screen(rows=1, cols=3)
    screen.put_char("a")
    screen.put_char("a")
    screen.put_char("a")
    instances = build_instances(screen, font, atlas)
    slots = {(inst["atlas_col"], inst["atlas_row"]) for inst in instances}
    assert len(slots) == 1  # same glyph -> same atlas slot, not re-packed


def test_build_instances_bold_cell_gets_a_different_atlas_slot(font, atlas):
    screen = Screen(rows=1, cols=2)
    screen.put_char("a")
    screen.sgr([1])  # bold
    screen.put_char("a")
    instances = build_instances(screen, font, atlas)
    regular_slot = (instances[0]["atlas_col"], instances[0]["atlas_row"])
    bold_slot = (instances[1]["atlas_col"], instances[1]["atlas_row"])
    assert regular_slot != bold_slot


def test_build_instances_underline_sets_flag(font, atlas):
    screen = Screen(rows=1, cols=2)
    screen.put_char("a")
    screen.sgr([4])  # underline
    screen.put_char("a")
    instances = build_instances(screen, font, atlas)
    assert instances[0]["flags"][0] == 0.0
    assert instances[1]["flags"][0] == 1.0


def test_build_instances_show_cursor_false_leaves_cell_unchanged(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.put_char("a")
    instances = build_instances(screen, font, atlas, show_cursor=False)
    assert list(instances[0]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[0]["bg"]) == list(srgb_color(0, 0, 0))
    assert list(instances[0]["cursor"]) == [0.0, 0.0, 0.0, 0.0]


def test_build_instances_block_cursor_swaps_colors_at_cursor_cell(font, atlas):
    screen = Screen(rows=1, cols=2)
    screen.put_char("a")
    screen.put_char("b")
    screen.cursor_position(1, 1)  # 1-indexed CUP -> back to col 0
    instances = build_instances(
        screen, font, atlas, show_cursor=True, cursor_color=(1, 2, 3), cursor_text_color=(4, 5, 6)
    )
    assert list(instances[0]["bg"]) == list(srgb_color(1, 2, 3))
    assert list(instances[0]["fg"]) == list(srgb_color(4, 5, 6))
    # the non-cursor cell is untouched
    assert list(instances[1]["bg"]) == list(srgb_color(0, 0, 0))
    assert list(instances[1]["fg"]) == list(srgb_color(255, 255, 255))


def test_build_instances_underline_cursor_does_not_recolor_the_glyph(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.put_char("a")
    screen.set_cursor_shape(4)  # steady underline
    instances = build_instances(screen, font, atlas, show_cursor=True, cursor_color=(9, 9, 9))
    # text/bg colors stay the cell's real ones -- only the cursor field carries
    # the bar color, the shader draws the actual decoration.
    assert list(instances[0]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[0]["bg"]) == list(srgb_color(0, 0, 0))
    cr, cg, cb, _ = srgb_color(9, 9, 9)
    assert list(instances[0]["cursor"]) == [cr, cg, cb, 1.0]


def test_build_instances_beam_cursor_sets_shape_code_two(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.set_cursor_shape(6)  # blinking beam
    instances = build_instances(screen, font, atlas, show_cursor=True, cursor_color=(9, 9, 9))
    assert instances[0]["cursor"][3] == 2.0


def test_build_instances_cursor_shape_none_draws_no_decoration(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.put_char("a")
    screen.set_cursor_shape(7)  # DECSCUSR "no cursor shape"
    instances = build_instances(screen, font, atlas, show_cursor=True, cursor_color=(9, 9, 9))
    assert list(instances[0]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[0]["bg"]) == list(srgb_color(0, 0, 0))
    assert list(instances[0]["cursor"]) == [0.0, 0.0, 0.0, 0.0]


def test_build_instances_selected_cells_use_selection_colors(font, atlas):
    screen = Screen(rows=1, cols=3)
    screen.put_char("a")
    screen.put_char("b")
    screen.put_char("c")
    screen.start_selection(0, 0)
    screen.update_selection(0, 1)
    instances = build_instances(screen, font, atlas, selection_fg=(1, 2, 3), selection_bg=(4, 5, 6))
    assert list(instances[0]["fg"]) == list(srgb_color(1, 2, 3))
    assert list(instances[0]["bg"]) == list(srgb_color(4, 5, 6))
    assert list(instances[1]["fg"]) == list(srgb_color(1, 2, 3))
    assert list(instances[1]["bg"]) == list(srgb_color(4, 5, 6))
    # cell 2 is outside the selection -- untouched default colors
    assert list(instances[2]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[2]["bg"]) == list(srgb_color(0, 0, 0))


def test_build_instances_no_selection_leaves_colors_untouched(font, atlas):
    screen = Screen(rows=1, cols=1)
    screen.put_char("a")
    instances = build_instances(screen, font, atlas, selection_fg=(1, 2, 3), selection_bg=(4, 5, 6))
    assert list(instances[0]["fg"]) == list(srgb_color(255, 255, 255))
    assert list(instances[0]["bg"]) == list(srgb_color(0, 0, 0))


def test_build_instances_block_cursor_wins_over_selection_on_the_same_cell(font, atlas):
    screen = Screen(rows=1, cols=2)
    screen.put_char("a")
    screen.put_char("b")
    screen.cursor_position(1, 1)  # back to col 0
    screen.start_selection(0, 0)
    screen.update_selection(0, 1)
    instances = build_instances(
        screen, font, atlas,
        show_cursor=True, cursor_color=(9, 9, 9), cursor_text_color=(8, 8, 8),
        selection_fg=(1, 2, 3), selection_bg=(4, 5, 6),
    )
    # cursor cell (0,0): cursor colors win
    assert list(instances[0]["bg"]) == list(srgb_color(9, 9, 9))
    assert list(instances[0]["fg"]) == list(srgb_color(8, 8, 8))
    # the other selected cell (0,1) still gets the selection colors
    assert list(instances[1]["fg"]) == list(srgb_color(1, 2, 3))
    assert list(instances[1]["bg"]) == list(srgb_color(4, 5, 6))


def _atlas_slot_for(font, atlas, ch):
    glyph_id = font.glyph_id_for_char(ch, bold=False)
    return atlas.get_or_add((glyph_id, False), font.rasterize(glyph_id, bold=False))


def test_build_instances_renders_scrollback_when_scrolled_back(font, atlas):
    screen = Screen(rows=1, cols=3, scrollback_limit=10)
    for i, ch in enumerate("abc"):
        screen.grid[0][i].char = ch
    screen.cursor_position(1, 1)
    screen.linefeed()  # "abc" -> scrollback, grid now blank
    for i, ch in enumerate("xyz"):
        screen.grid[0][i].char = ch
    instances = build_instances(screen, font, atlas)  # scroll_offset == 0: live grid
    live_slot = _atlas_slot_for(font, atlas, "x")
    assert (instances[0]["atlas_col"], instances[0]["atlas_row"]) == (live_slot.col, live_slot.row)
    screen.scroll_view(1)
    instances = build_instances(screen, font, atlas)
    back_slot = _atlas_slot_for(font, atlas, "a")
    assert (instances[0]["atlas_col"], instances[0]["atlas_row"]) == (back_slot.col, back_slot.row)


def test_build_instances_suppresses_cursor_and_selection_while_scrolled_back(font, atlas):
    screen = Screen(rows=1, cols=3, scrollback_limit=10)
    screen.put_char("a")
    screen.cursor_position(1, 1)
    screen.linefeed()  # real scrollback line to scroll into
    screen.start_selection(0, 0)
    screen.update_selection(0, 1)
    screen.scroll_view(1)
    instances = build_instances(
        screen, font, atlas,
        show_cursor=True, cursor_color=(9, 9, 9), cursor_text_color=(8, 8, 8),
        selection_fg=(1, 2, 3), selection_bg=(4, 5, 6),
    )
    for cell in instances:
        assert list(cell["cursor"]) == [0.0, 0.0, 0.0, 0.0]
        assert list(cell["fg"]) != list(srgb_color(1, 2, 3))

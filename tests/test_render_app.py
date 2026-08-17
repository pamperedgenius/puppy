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

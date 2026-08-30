"""CellRenderer tests: real wgpu + offscreen canvas + real pixel readback.
No visible window. Skips if no GPU adapter or the render deps aren't
installed.
"""
import shutil
import subprocess

import numpy as np
import pytest

wgpu = pytest.importorskip("wgpu")
offscreen = pytest.importorskip("rendercanvas.offscreen")
pytest.importorskip("freetype")
pytest.importorskip("uharfbuzz")

from puppy.render.atlas import GlyphAtlas
from puppy.render.cell_renderer import INSTANCE_DTYPE, CellRenderer
from puppy.render.color import srgb_color
from puppy.render.font import FontRenderer, GlyphBitmap
from puppy.render.gpu import GpuContext


def _adapter_available() -> bool:
    try:
        canvas = offscreen.RenderCanvas(size=(2, 2))
        wgpu.gpu.request_adapter_sync(canvas=canvas)
        return True
    except Exception:
        return False


def _find_monospace_font():
    if not shutil.which("fc-match"):
        return None
    result = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True)
    return result.stdout.strip() or None


_FONT_PATH = _find_monospace_font()
pytestmark = pytest.mark.skipif(
    not _adapter_available() or _FONT_PATH is None,
    reason="needs a GPU adapter and a monospace font",
)


def test_partially_inked_glyph_produces_exact_fg_and_bg_pixels():
    # Synthetic glyph, not a real font: left half of the cell fully inked
    # (alpha=255), right half fully transparent (alpha=0) -- proves the whole
    # pipeline (texture upload, storage buffer, uniform buffer, quad
    # generation, UV mapping, fragment blend) with exact, unambiguous colors.
    cell_w, cell_h = 4, 4
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    bitmap = GlyphBitmap(
        width=cell_w,
        height=cell_h,
        bearing_x=0,
        bearing_y=cell_h,
        pixels=bytes([255, 255, 0, 0] * cell_h),  # left half ink, right half none
    )
    slot = atlas.get_or_add(1, bitmap)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 0, 0), srgb_color(0, 0, 255), (0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[1, 0]) == [255, 0, 0, 255]  # left half: pure fg (red)
    assert list(img[1, 3]) == [0, 0, 255, 255]  # right half: pure bg (blue)


def test_real_rasterized_glyph_renders_something_not_just_flat_bg():
    font = FontRenderer(_FONT_PATH, pixel_size=16)
    atlas = GlyphAtlas(cell_width=font.cell_width, cell_height=font.cell_height, ascender=font.ascender)
    glyph_id = font.glyph_id_for_char("M")
    bitmap = font.rasterize(glyph_id)
    slot = atlas.get_or_add(glyph_id, bitmap)

    canvas = offscreen.RenderCanvas(size=(font.cell_width, font.cell_height))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 255, 255), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    # 'M' has real ink somewhere and real empty margin somewhere -- must not
    # be a flat single-color cell in either direction.
    unique_colors = {tuple(px) for row in img for px in row}
    assert len(unique_colors) > 1
    assert (0, 0, 0, 255) in unique_colors  # at least some pure background survives


def test_two_cells_render_independently():
    cell_w, cell_h = 4, 4
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    full_ink = GlyphBitmap(width=cell_w, height=cell_h, bearing_x=0, bearing_y=cell_h, pixels=bytes([255] * cell_w * cell_h))
    slot = atlas.get_or_add(1, full_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w * 2, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=2)

    instances = np.zeros(2, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 0, 0), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    instances[1] = (1, 0, slot.col, slot.row, srgb_color(0, 255, 0), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[2, 1]) == [255, 0, 0, 255]  # left cell: red
    assert list(img[2, 5]) == [0, 255, 0, 255]  # right cell: green


def test_underline_draws_fg_colored_band_at_real_metrics_position():
    cell_w, cell_h = 8, 8
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    no_ink = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    slot = atlas.get_or_add(1, no_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    # underline_y=6, thickness=2 -> band covers pixel rows with center in [5, 7]
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1, underline_y=6, underline_thickness=2)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 0, 0), srgb_color(0, 0, 0), (1.0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[0, 3]) == [0, 0, 0, 255]  # top of cell: plain bg, no underline
    assert list(img[6, 3]) == [255, 0, 0, 255]  # underline row: fg color


def test_resize_grows_grid_and_new_cells_render_correctly():
    # Real bug this guards against: before CellRenderer.resize() existed, the
    # instance buffer/uniform screen_size were fixed at construction time, so
    # a grid grown after a real window resize would either not fit in the
    # buffer or render at the stale (pre-resize) screen_size, stretching the
    # grid to fill whatever the real surface actually became -- niri in
    # particular always overrides an app's requested initial window size.
    cell_w, cell_h = 4, 4
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    full_ink = GlyphBitmap(width=cell_w, height=cell_h, bearing_x=0, bearing_y=cell_h, pixels=bytes([255] * cell_w * cell_h))
    slot = atlas.get_or_add(1, full_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w * 2, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1)  # starts smaller than the real 2-cell-wide surface

    renderer.resize(rows=1, cols=2)
    instances = np.zeros(2, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 0, 0), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    instances[1] = (1, 0, slot.col, slot.row, srgb_color(0, 255, 0), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[2, 1]) == [255, 0, 0, 255]  # left cell: red, at the correct (post-resize) NDC scale
    assert list(img[2, 5]) == [0, 255, 0, 255]  # right cell: green, not stretched/misaligned


def test_resize_to_same_dimensions_is_a_no_op():
    cell_w, cell_h = 4, 4
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1)
    buffer_before = renderer._instance_buffer
    renderer.resize(rows=1, cols=1)
    assert renderer._instance_buffer is buffer_before


def test_cursor_underline_draws_bar_in_cursor_color_at_bottom_of_cell():
    cell_w, cell_h = 8, 8
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    no_ink = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    slot = atlas.get_or_add(1, no_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1, underline_y=6, underline_thickness=2)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    # fg/bg deliberately plain black/white so the cursor's distinct green bar
    # color is unambiguous against either; cursor shape code 1 = underline.
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 255, 255), srgb_color(0, 0, 0), (0, 0, 0, 0), (*srgb_color(0, 255, 0)[:3], 1.0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[0, 3]) == [0, 0, 0, 255]  # top of cell: plain bg, no cursor bar
    assert list(img[7, 3]) == [0, 255, 0, 255]  # bottom edge: cursor's own color, not fg's


def test_cursor_beam_draws_bar_in_cursor_color_at_left_edge_of_cell():
    cell_w, cell_h = 8, 8
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    no_ink = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    slot = atlas.get_or_add(1, no_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1, underline_y=6, underline_thickness=2)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    # cursor shape code 2 = beam.
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 255, 255), srgb_color(0, 0, 0), (0, 0, 0, 0), (*srgb_color(0, 255, 0)[:3], 2.0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[3, 0]) == [0, 255, 0, 255]  # left edge: cursor's own color
    assert list(img[3, 7]) == [0, 0, 0, 255]  # right edge: plain bg, no beam there


def test_cursor_field_zero_shape_draws_no_decoration():
    cell_w, cell_h = 8, 8
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    no_ink = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    slot = atlas.get_or_add(1, no_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1, underline_y=6, underline_thickness=2)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 255, 255), srgb_color(0, 0, 0), (0, 0, 0, 0), (0, 255, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[7, 3]) == [0, 0, 0, 255]  # no bar despite a nonzero cursor color, shape is 0
    assert list(img[3, 0]) == [0, 0, 0, 255]


def test_underline_flag_off_never_draws_a_band():
    cell_w, cell_h = 8, 8
    atlas = GlyphAtlas(cell_width=cell_w, cell_height=cell_h, ascender=cell_h)
    no_ink = GlyphBitmap(width=0, height=0, bearing_x=0, bearing_y=0, pixels=b"")
    slot = atlas.get_or_add(1, no_ink)

    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = CellRenderer(gpu, atlas, rows=1, cols=1, underline_y=6, underline_thickness=2)

    instances = np.zeros(1, dtype=INSTANCE_DTYPE)
    instances[0] = (0, 0, slot.col, slot.row, srgb_color(255, 0, 0), srgb_color(0, 0, 0), (0.0, 0, 0, 0), (0, 0, 0, 0))
    canvas.request_draw(lambda: renderer.render(instances))
    img = canvas.draw()

    assert list(img[6, 3]) == [0, 0, 0, 255]  # same row that would be underlined -- plain bg instead

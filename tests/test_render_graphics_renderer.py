"""GraphicsRenderer tests: real wgpu + offscreen canvas + real pixel readback.
No visible window. Skips if no GPU adapter or the render deps aren't
installed. Same discipline as test_render_cell_renderer.py -- exact-color
proofs, not "didn't crash."
"""
import numpy as np
import pytest

wgpu = pytest.importorskip("wgpu")
offscreen = pytest.importorskip("rendercanvas.offscreen")

from puppy.graphics import GraphicsManager
from puppy.render.gpu import GpuContext
from puppy.render.graphics_renderer import GraphicsRenderer


def _adapter_available() -> bool:
    try:
        canvas = offscreen.RenderCanvas(size=(2, 2))
        wgpu.gpu.request_adapter_sync(canvas=canvas)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _adapter_available(), reason="needs a real GPU adapter")


def _load_rgb_image(graphics: GraphicsManager, image_id: int, width: int, height: int, pixel: tuple[int, int, int], row: int, col: int) -> None:
    import base64

    payload = bytes(pixel) * (width * height)
    graphics.handle_command(
        {"a": "T", "f": "24", "s": str(width), "v": str(height), "i": str(image_id)},
        base64.b64encode(payload),
        cursor_row=row,
        cursor_col=col,
    )


def _load_rgba_image(graphics: GraphicsManager, image_id: int, width: int, height: int, pixel: tuple[int, int, int, int], row: int, col: int) -> None:
    import base64

    payload = bytes(pixel) * (width * height)
    graphics.handle_command(
        {"a": "T", "f": "32", "s": str(width), "v": str(height), "i": str(image_id)},
        base64.b64encode(payload),
        cursor_row=row,
        cursor_col=col,
    )


def test_rgb_image_renders_exact_color_at_placement_and_leaves_rest_clear():
    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w * 2, cell_h * 2))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    graphics = GraphicsManager()
    _load_rgb_image(graphics, 1, width=cell_w, height=cell_h, pixel=(255, 0, 0), row=0, col=0)

    def draw():
        gpu.clear((0.0, 0.0, 0.0, 1.0))  # base color: black, then draw the image on top
        renderer.render(graphics, cols=2, rows=2, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [255, 0, 0, 255]  # inside the placed image: pure red
    assert list(img[cell_h + 1, cell_w + 1]) == [0, 0, 0, 255]  # far corner: untouched black clear


def test_rgba_image_alpha_blends_over_existing_content():
    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    graphics = GraphicsManager()
    _load_rgba_image(graphics, 1, width=cell_w, height=cell_h, pixel=(255, 0, 0, 0), row=0, col=0)  # fully transparent red

    def draw():
        gpu.clear((0.0, 0.0, 1.0, 1.0))  # base color: blue
        renderer.render(graphics, cols=1, rows=1, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    # alpha=0 red over blue -- blue must survive untouched, proving the blend
    # is real alpha compositing, not an opaque overwrite
    assert list(img[1, 1]) == [0, 0, 255, 255]


def test_image_auto_sized_from_pixel_dimensions_spans_multiple_cells():
    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w * 3, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    graphics = GraphicsManager()
    # 8px wide image with 4px cells -> spans exactly 2 cells (ceil(8/4)=2)
    _load_rgb_image(graphics, 1, width=cell_w * 2, height=cell_h, pixel=(0, 255, 0), row=0, col=0)

    def draw():
        gpu.clear((0.0, 0.0, 0.0, 1.0))
        renderer.render(graphics, cols=3, rows=1, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [0, 255, 0, 255]  # first cell: covered
    assert list(img[1, cell_w + 1]) == [0, 255, 0, 255]  # second cell: covered
    assert list(img[1, cell_w * 2 + 1]) == [0, 0, 0, 255]  # third cell: untouched


def test_placement_positioned_by_row_and_col():
    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w * 2, cell_h * 2))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    graphics = GraphicsManager()
    _load_rgb_image(graphics, 1, width=cell_w, height=cell_h, pixel=(255, 255, 0), row=1, col=1)

    def draw():
        gpu.clear((0.0, 0.0, 0.0, 1.0))
        renderer.render(graphics, cols=2, rows=2, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [0, 0, 0, 255]  # top-left cell (row 0, col 0): untouched
    assert list(img[cell_h + 1, cell_w + 1]) == [255, 255, 0, 255]  # row 1, col 1: covered


def test_higher_z_index_draws_on_top_regardless_of_placement_order():
    import base64

    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    graphics = GraphicsManager()
    # Placed first but with the higher z-index -- must still end up on top.
    graphics.handle_command(
        {"a": "T", "f": "24", "s": str(cell_w), "v": str(cell_h), "i": "1", "z": "5"},
        base64.b64encode(bytes((0, 255, 0)) * (cell_w * cell_h)),
        cursor_row=0, cursor_col=0,
    )
    graphics.handle_command(
        {"a": "T", "f": "24", "s": str(cell_w), "v": str(cell_h), "i": "2", "z": "-1"},
        base64.b64encode(bytes((255, 0, 0)) * (cell_w * cell_h)),
        cursor_row=0, cursor_col=0,
    )

    def draw():
        gpu.clear((0.0, 0.0, 0.0, 1.0))
        renderer.render(graphics, cols=1, rows=1, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [0, 255, 0, 255]  # green (z=5) wins over red (z=-1)


def test_placement_crop_shows_only_the_requested_source_rect():
    cell_w, cell_h = 2, 2
    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)

    import base64

    # 4x4 image: left half red, right half blue.
    width = height = 4
    row = [255, 0, 0] * 2 + [0, 0, 255] * 2
    payload = bytes(row) * height
    graphics = GraphicsManager()
    graphics.handle_command(
        {"a": "t", "f": "24", "s": str(width), "v": str(height), "i": "1"},  # transmit only, no placement yet
        base64.b64encode(payload), cursor_row=0, cursor_col=0,
    )
    # Crop to just the right (blue) half, put into a single cell.
    graphics.handle_command({"a": "p", "i": "1", "x": "2", "w": "2", "c": "1", "r": "1"}, b"", cursor_row=0, cursor_col=0)

    def draw():
        gpu.clear((0.0, 0.0, 0.0, 1.0))
        renderer.render(graphics, cols=1, rows=1, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [0, 0, 255, 255]  # cropped to the blue half only


def test_no_placements_leaves_frame_untouched():
    cell_w, cell_h = 4, 4
    canvas = offscreen.RenderCanvas(size=(cell_w, cell_h))
    gpu = GpuContext.create(canvas)
    renderer = GraphicsRenderer(gpu)
    graphics = GraphicsManager()  # nothing transmitted

    def draw():
        gpu.clear((0.0, 1.0, 0.0, 1.0))
        renderer.render(graphics, cols=1, rows=1, cell_width=cell_w, cell_height=cell_h)

    canvas.request_draw(draw)
    img = canvas.draw()

    assert list(img[1, 1]) == [0, 255, 0, 255]

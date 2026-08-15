"""GPU-backed tests: real wgpu device + offscreen canvas, real pixel readback.

No visible window is ever created here (rendercanvas.offscreen), consistent
with not popping GUI windows for automated/repeated testing. If no GPU adapter
is available in the running environment, these skip rather than fail the suite.
"""
import pytest

wgpu = pytest.importorskip("wgpu")
offscreen = pytest.importorskip("rendercanvas.offscreen")

from puppy.render.color import srgb_color
from puppy.render.gpu import GpuContext


def _adapter_available() -> bool:
    try:
        canvas = offscreen.RenderCanvas(size=(2, 2))
        wgpu.gpu.request_adapter_sync(canvas=canvas)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _adapter_available(), reason="no GPU adapter available")


def test_gpu_context_create_configures_surface():
    canvas = offscreen.RenderCanvas(size=(4, 4))
    ctx = GpuContext.create(canvas)
    assert ctx.device is not None
    assert ctx.format  # a real format string, e.g. "bgra8unorm-srgb"


def test_clear_produces_exact_srgb_color():
    canvas = offscreen.RenderCanvas(size=(4, 4))
    ctx = GpuContext.create(canvas)
    canvas.request_draw(lambda: ctx.clear(srgb_color(255, 0, 128)))
    img = canvas.draw()
    pixel = img[0, 0]
    assert list(pixel) == [255, 0, 128, 255]


def test_clear_is_uniform_across_the_surface():
    canvas = offscreen.RenderCanvas(size=(4, 4))
    ctx = GpuContext.create(canvas)
    canvas.request_draw(lambda: ctx.clear(srgb_color(10, 20, 30)))
    img = canvas.draw()
    assert (img[:, :, 0] == 10).all()
    assert (img[:, :, 1] == 20).all()
    assert (img[:, :, 2] == 30).all()

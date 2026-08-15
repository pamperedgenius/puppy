"""wgpu adapter/device/surface setup, decoupled from which canvas backs it.

Works identically against rendercanvas.offscreen.RenderCanvas (used by tests --
headless, no real window, real pixel readback via numpy) and
rendercanvas.glfw.RenderCanvas (the live interactive window) -- same
canvas-agnostic pattern as Parser/Screen: write the logic once, feed it either a
real or a test double.
"""
from __future__ import annotations

from dataclasses import dataclass

import wgpu


@dataclass
class GpuContext:
    canvas: object
    adapter: object
    device: object
    context: object
    format: str

    @classmethod
    def create(cls, canvas) -> "GpuContext":
        adapter = wgpu.gpu.request_adapter_sync(canvas=canvas)
        device = adapter.request_device_sync()
        context = canvas.get_context("wgpu")
        # get_preferred_format() commonly returns an sRGB-encoded format (e.g.
        # bgra8unorm-srgb). Confirmed empirically (round-tripped clear+readback
        # against the real GPU, exact match across the whole 0-255 range): the
        # GPU treats clear_value as LINEAR and gamma-*encodes* it on write. Any
        # theme/SGR color, which is always specified as sRGB bytes (the normal
        # display convention), must be *decoded* sRGB->linear with
        # puppy.render.color.srgb_to_linear before being passed here or to a
        # vertex color -- passing the raw byte/255 value directly would get
        # double-encoded by the hardware and render too bright/washed out.
        fmt = context.get_preferred_format(adapter)
        context.configure(device=device, format=fmt)
        return cls(canvas=canvas, adapter=adapter, device=device, context=context, format=fmt)

    def clear(self, color: tuple[float, float, float, float]) -> None:
        """Clears the whole surface to `color` (r, g, b, a in 0.0-1.0, linear
        space per WebGPU's clear_value semantics -- see the sRGB note above)
        and presents one frame. Placeholder for the real cell-grid render pass;
        this exists as the first, minimal, provable rendering milestone.
        """
        texture = self.context.get_current_texture()
        encoder = self.device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": texture.create_view(),
                    "resolve_target": None,
                    "clear_value": color,
                    "load_op": wgpu.LoadOp.clear,
                    "store_op": wgpu.StoreOp.store,
                }
            ]
        )
        pass_.end()
        self.device.queue.submit([encoder.finish()])

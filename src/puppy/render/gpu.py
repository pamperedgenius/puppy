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
from wgpu.backends.wgpu_native.extras import set_instance_extras

# Real, measured launch-time bug found and fixed here (2026-09-02). On this
# project's dev machine (a hybrid Intel iGPU + NVIDIA dGPU laptop), a plain
# `wgpu.gpu.request_adapter_sync()` cost ~1.7-1.8s instead of the expected
# ~0.1-0.3s -- root-caused with strace + symbol resolution, not guessed:
# wgpu-native's default Instance probes *every* backend (Vulkan AND GL) the
# moment the instance is created, before any later adapter-request filtering
# even runs. The GL/EGL probe loads NVIDIA's GLVND vendor library
# (`libnvidia-glsi.so`) and opens `/dev/nvidia0` purely to enumerate it as a
# candidate adapter -- and on this laptop that's enough to wake the NVIDIA GPU
# from PCIe runtime suspend (confirmed live: `power/runtime_status` for the
# NVIDIA PCI device flips `suspended` -> `active` at exactly this call, every
# time), even though this project never ends up using the GL backend or the
# NVIDIA adapter at all (confirmed via `wgpu.gpu.enumerate_adapters_sync()`:
# NVIDIA is only reachable via OpenGL here, never Vulkan -- puppy always runs
# on the Intel adapter via Vulkan). Five different Vulkan-loader-level env
# vars (`VK_ICD_FILENAMES`, `VK_DRIVER_FILES`, `VK_LOADER_DRIVERS_SELECT`,
# `VK_LOADER_LAYERS_DISABLE`, Mesa's own `NODEVICE_SELECT`) were tried first
# and *all* failed to prevent the wake -- confirmed via `VK_LOADER_DEBUG=all`
# that the restrictions were genuinely taking effect at the Vulkan level, yet
# the wake still happened regardless, because the actual cause sits one layer
# up, at wgpu-native's own Instance creation, which no Vulkan-spec env var can
# reach. `set_instance_extras` restricts the *instance itself* (not just a
# later adapter-request filter) to `Primary` backends (Vulkan/Metal/DX12/
# BrowserWebGPU -- i.e. real, non-legacy graphics APIs, excluding only GL/
# GLES), which avoids the GL/EGL probe -- and with it, the NVIDIA touch --
# entirely. Confirmed live and repeatedly (multiple cold trials, each after a
# real ~15s idle gap so the dGPU had genuinely re-suspended): with this set,
# the NVIDIA PCI device's `power/runtime_status` stays `suspended` throughout
# adapter request, and `request_adapter_sync` drops to a consistent ~0.1s. No
# functional change -- this project never used the GL backend or the NVIDIA
# adapter either way. Must be set before the very first
# `request_adapter_sync`/`enumerate_adapters_sync` call in the process --
# wgpu-native raises if its (process-global, C-level) instance already
# exists. Set here at *module import* time rather than lazily inside
# `create()`: several test files probe `wgpu.gpu.request_adapter_sync`
# directly (their own `_adapter_available()` skip-check) in the same module
# that imports `GpuContext` -- a lazy call inside `create()` would run too
# late whenever such a probe executes first, since Python only imports this
# module once and every one of those test files imports it (for `GpuContext`)
# before defining/running its own probe.
set_instance_extras(backends=["Primary"])


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

"""GPU rendering of placed kitty-graphics images: a real texture + draw path,
separate from CellRenderer/GlyphAtlas (which is sized for fixed glyph-cell
slots, not arbitrary image dimensions).

Confirmed against kitty's real `~/Projects/kitty/kitty/shaders/graphics.slang`
+ `graphics.c` (`gpu_data_for_image`, `grman_update_layers`): images are drawn
as a single textured quad per placement -- `src_rect` in [0,1] texcoord space,
`dest_rect` in NDC with y flipped top-to-bottom (top=+1, bottom=-1), same
convention CellRenderer's own vertex shader already uses -- composited with a
premultiplied-alpha "over" blend. Kitty's real shader premultiplies in the
fragment shader itself (`texture_is_not_premultiplied = true` -> `color =
vec4_premul(color)`) because wire pixel data is straight (non-premultiplied)
alpha; this ports the same approach rather than assuming the texture is
already premultiplied.

sRGB handling matches the rule already established for clear_value/vertex
colors (see GpuContext / color.py): raw image bytes are sRGB-encoded (the
normal convention, confirmed against `PIX_FMT_RGB[A]` in every terminal
graphics-protocol implementation, kitty included), so the source texture is
created as `rgba8unorm_srgb` -- the GPU auto-decodes to linear on sample,
matching the surface's own auto-encode-on-write, instead of needing a manual
`srgb_to_linear` step the way plain per-vertex colors do.

v2 scope (this pass): placements now carry real crop (`src_x`/`src_y`/
`src_width`/`src_height`, 0 meaning "to the image's edge") and explicit
`num_cols`/`num_rows`, both resolved here at render time from the `Placement`
fields `puppy.graphics` now populates for `a=p`/`a=T`. Placements are drawn
sorted by `z_index` ascending (ties keep insertion order via Python's stable
sort) -- lower z-index draws first/behind, matching kitty's real ordering
intent, though not its exact below/negative/positive three-tier compositing
against the text grid itself (images always draw on top of the full cell
grid here, single pass -- see `puppy.graphics`'s module docstring for what
z-index does and doesn't cover in this project).

v1 scope, still true: no scrollback scrolling of images, one draw call per
placement with its own tiny per-placement uniform buffer (not kitty's
`group_count` instancing optimization -- fine at real-world image-placement
counts, revisit only if profiling ever shows otherwise, same "don't
pre-optimize" rule as the parser).
"""
from __future__ import annotations

import math

import numpy as np
import wgpu

from ..graphics import GraphicsManager
from .gpu import GpuContext

_SHADER_SOURCE = """
struct Uniforms {
    src_rect: vec4<f32>,   // left, top, right, bottom in texcoord space [0,1]
    dest_rect: vec4<f32>,  // left, top, right, bottom in NDC
};

@group(0) @binding(0) var<uniform> u: Uniforms;
@group(0) @binding(1) var img_tex: texture_2d<f32>;
@group(0) @binding(2) var img_samp: sampler;

struct VOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vidx: u32) -> VOut {
    var corner_x = array<f32, 6>(0.0, 1.0, 0.0, 1.0, 1.0, 0.0);
    var corner_y = array<f32, 6>(0.0, 0.0, 1.0, 0.0, 1.0, 1.0);
    let cx = corner_x[vidx];
    let cy = corner_y[vidx];

    let x = mix(u.dest_rect.x, u.dest_rect.z, cx);
    let y = mix(u.dest_rect.y, u.dest_rect.w, cy);
    let uu = mix(u.src_rect.x, u.src_rect.z, cx);
    let vv = mix(u.src_rect.y, u.src_rect.w, cy);

    var out: VOut;
    out.position = vec4<f32>(x, y, 0.0, 1.0);
    out.uv = vec2<f32>(uu, vv);
    return out;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let color = textureSample(img_tex, img_samp, in.uv);
    // Premultiply -- source texture data is straight (non-premultiplied)
    // alpha, confirmed against kitty's real graphics.slang.
    return vec4<f32>(color.rgb * color.a, color.a);
}
"""

_UNIFORM_DTYPE = np.dtype([("src_rect", "f4", 4), ("dest_rect", "f4", 4)])


class GraphicsRenderer:
    def __init__(self, gpu: GpuContext) -> None:
        self.gpu = gpu
        device = gpu.device
        shader = device.create_shader_module(code=_SHADER_SOURCE)
        self._sampler = device.create_sampler()
        self._bind_group_layout = device.create_bind_group_layout(
            entries=[
                {"binding": 0, "visibility": wgpu.ShaderStage.VERTEX | wgpu.ShaderStage.FRAGMENT, "buffer": {"type": wgpu.BufferBindingType.uniform}},
                {"binding": 1, "visibility": wgpu.ShaderStage.FRAGMENT, "texture": {"sample_type": wgpu.TextureSampleType.float}},
                {"binding": 2, "visibility": wgpu.ShaderStage.FRAGMENT, "sampler": {"type": wgpu.SamplerBindingType.filtering}},
            ]
        )
        pipeline_layout = device.create_pipeline_layout(bind_group_layouts=[self._bind_group_layout])
        self._pipeline = device.create_render_pipeline(
            layout=pipeline_layout,
            vertex={"module": shader, "entry_point": "vs_main"},
            fragment={
                "module": shader,
                "entry_point": "fs_main",
                "targets": [
                    {
                        "format": gpu.format,
                        "blend": {
                            "color": {
                                "src_factor": wgpu.BlendFactor.one,
                                "dst_factor": wgpu.BlendFactor.one_minus_src_alpha,
                                "operation": wgpu.BlendOperation.add,
                            },
                            "alpha": {
                                "src_factor": wgpu.BlendFactor.one,
                                "dst_factor": wgpu.BlendFactor.one_minus_src_alpha,
                                "operation": wgpu.BlendOperation.add,
                            },
                        },
                    }
                ],
            },
            primitive={"topology": wgpu.PrimitiveTopology.triangle_list},
        )
        # image_id -> (Image instance last uploaded, gpu texture). Identity
        # check (not just id) so a redefinition of the same image id (a real,
        # if v1-unhandled-elsewhere, retransmission case) re-uploads instead
        # of silently keeping stale pixels.
        self._textures: dict[int, tuple[object, object]] = {}

    def _texture_for(self, image) -> object:
        cached = self._textures.get(image.id)
        if cached is not None and cached[0] is image:
            return cached[1]
        device = self.gpu.device
        if image.format == 24:
            rgb = np.frombuffer(image.data, dtype=np.uint8).reshape(image.height, image.width, 3)
            alpha = np.full((image.height, image.width, 1), 255, dtype=np.uint8)
            data = np.ascontiguousarray(np.dstack([rgb, alpha])).tobytes()
        else:
            data = image.data
        texture = device.create_texture(
            size=(image.width, image.height, 1),
            format=wgpu.TextureFormat.rgba8unorm_srgb,
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
        )
        device.queue.write_texture(
            {"texture": texture},
            data,
            {"bytes_per_row": image.width * 4},
            (image.width, image.height, 1),
        )
        self._textures[image.id] = (image, texture)
        return texture

    def render(self, graphics: GraphicsManager, cols: int, rows: int, cell_width: float, cell_height: float) -> None:
        """Draws every current placement on top of whatever CellRenderer
        already drew into the current frame's texture this frame (load_op
        `load`, not `clear` -- this must run after the cell-grid draw call,
        same command-buffer submission model as CellRenderer.render)."""
        if not graphics.placements:
            return
        device = self.gpu.device
        texture_view = self.gpu.context.get_current_texture().create_view()
        encoder = device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": texture_view,
                    "resolve_target": None,
                    "load_op": wgpu.LoadOp.load,
                    "store_op": wgpu.StoreOp.store,
                }
            ]
        )
        pass_.set_pipeline(self._pipeline)
        dx = 2.0 / cols
        dy = 2.0 / rows
        # Ascending z-index draws first (behind); stable sort preserves
        # insertion order among equal z-indexes, same as kitty's own
        # image_id/ref_id tie-break for a real-world-equivalent result.
        for placement in sorted(graphics.placements, key=lambda pl: pl.z_index):
            image = graphics.images.get(placement.image_id)
            if image is None:
                continue
            texture = self._texture_for(image)
            num_cols = placement.num_cols or max(1, math.ceil(image.width / cell_width))
            num_rows = placement.num_rows or max(1, math.ceil(image.height / cell_height))
            top = 1.0 - placement.row * dy
            left = -1.0 + placement.col * dx
            bottom = top - num_rows * dy
            right = left + num_cols * dx

            src_width = placement.src_width or (image.width - placement.src_x)
            src_height = placement.src_height or (image.height - placement.src_y)
            src_rect = (
                placement.src_x / image.width,
                placement.src_y / image.height,
                (placement.src_x + src_width) / image.width,
                (placement.src_y + src_height) / image.height,
            )

            uniforms = np.zeros(1, dtype=_UNIFORM_DTYPE)
            uniforms[0] = (src_rect, (left, top, right, bottom))
            uniform_buffer = device.create_buffer_with_data(data=uniforms.tobytes(), usage=wgpu.BufferUsage.UNIFORM)
            bind_group = device.create_bind_group(
                layout=self._bind_group_layout,
                entries=[
                    {"binding": 0, "resource": {"buffer": uniform_buffer, "offset": 0, "size": uniform_buffer.size}},
                    {"binding": 1, "resource": texture.create_view()},
                    {"binding": 2, "resource": self._sampler},
                ],
            )
            pass_.set_bind_group(0, bind_group)
            pass_.draw(6)
        pass_.end()
        device.queue.submit([encoder.finish()])

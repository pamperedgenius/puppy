"""GPU cell-grid renderer: one instanced draw call renders every terminal
cell as a quad sampling its glyph from a GlyphAtlas texture, composited over
the cell's background with the confirmed premultiplied "over" blend from
kitty's real alpha-blend.slang (`result = over + under*(1-over.a)`,
simplified here since bg is always fully opaque: `fg*alpha + bg*(1-alpha)`),
then an underline band (if flagged) drawn as a solid fg-colored line at the
font's real underline_y/underline_thickness metrics, then a cursor
underline/beam decoration bar (if flagged, in the cursor's own color,
independent of the text-underline flag/color above -- a block-shaped cursor
needs no shader support at all, it's built by simply swapping that one
instance's fg/bg to the cursor colors before upload, see app.py).

Verified against the real GPU before trusting this design: a partially-inked
synthetic glyph (left half alpha=255, right half alpha=0) produced exact
pure-fg-color pixels on the inked half and exact pure-bg-color pixels on the
transparent half -- proves the whole path (texture upload, storage-buffer
instance data, uniform buffer, quad generation, UV mapping, fragment blend)
end to end with real numeric pixel readback, not just "didn't crash." The
extended 10-float/40-byte Uniforms struct (needed for underline_y/thickness)
was verified empirically too: a throwaway shader read back exact values from
every field before this was trusted, not assumed from WGSL alignment rules
alone (a plain top-level uniform struct only needs its largest-member
alignment, 8 bytes for vec2<f32> here -- NOT the stricter 16-byte rule that
applies to storage-buffer array *elements*, confirmed by a 40-byte struct
round-tripping correctly).

v1 scope: this is the core blend + underline only. Kitty's real cell.slang
also does HSLuv-based automatic fg/bg contrast override, cursor/selection
compositing, strikethrough, and gamma-adjustment modes -- none of that is
built here, see PROGRESS.md. Bold is handled entirely on the CPU side (a
different, emboldened glyph rasterization gets its own atlas slot -- see
font.py/app.py), not in this shader.
"""
from __future__ import annotations

import numpy as np
import wgpu

from .atlas import GlyphAtlas
from .gpu import GpuContext

_SHADER_SOURCE = """
struct Instance {
    col: f32,
    row: f32,
    atlas_col: f32,
    atlas_row: f32,
    fg: vec4<f32>,
    bg: vec4<f32>,
    flags: vec4<f32>,  // x = underline (0 or 1), y/z/w reserved
    cursor: vec4<f32>,  // rgb = cursor bar color, w = shape (0=none, 1=underline, 2=beam)
};

struct Uniforms {
    screen_size: vec2<f32>,
    cell_size: vec2<f32>,
    atlas_grid: vec2<f32>,
    underline: vec2<f32>,  // x = underline_y (px from cell top), y = thickness (px)
    _pad: vec2<f32>,
};

@group(0) @binding(0) var<storage, read> instances: array<Instance>;
@group(0) @binding(1) var<uniform> u: Uniforms;
@group(0) @binding(2) var atlas_tex: texture_2d<f32>;
@group(0) @binding(3) var atlas_samp: sampler;

struct VOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) fg: vec4<f32>,
    @location(2) bg: vec4<f32>,
    @location(3) cell_v: f32,
    @location(4) underline: f32,
    @location(5) cell_u: f32,
    @location(6) cursor: vec4<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vidx: u32, @builtin(instance_index) iidx: u32) -> VOut {
    let inst = instances[iidx];
    // 2 triangles, 6 verts: TL, TR, BL / TR, BR, BL
    var corner_x = array<f32, 6>(0.0, 1.0, 0.0, 1.0, 1.0, 0.0);
    var corner_y = array<f32, 6>(0.0, 0.0, 1.0, 0.0, 1.0, 1.0);
    let cx = corner_x[vidx];
    let cy = corner_y[vidx];

    let px = (inst.col + cx) * u.cell_size.x;
    let py = (inst.row + cy) * u.cell_size.y;
    let ndc_x = (px / u.screen_size.x) * 2.0 - 1.0;
    let ndc_y = 1.0 - (py / u.screen_size.y) * 2.0;

    var out: VOut;
    out.position = vec4<f32>(ndc_x, ndc_y, 0.0, 1.0);
    out.uv = vec2<f32>((inst.atlas_col + cx) / u.atlas_grid.x, (inst.atlas_row + cy) / u.atlas_grid.y);
    out.fg = inst.fg;
    out.bg = inst.bg;
    out.cell_v = cy;
    out.cell_u = cx;
    out.underline = inst.flags.x;
    out.cursor = inst.cursor;
    return out;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let alpha = textureSample(atlas_tex, atlas_samp, in.uv).r;
    var rgb = in.fg.rgb * alpha + in.bg.rgb * (1.0 - alpha);
    if (in.underline > 0.5) {
        let pixel_y = in.cell_v * u.cell_size.y;
        let half_thickness = u.underline.y * 0.5;
        if (abs(pixel_y - u.underline.x) <= half_thickness) {
            rgb = in.fg.rgb;
        }
    }
    // Cursor underline/beam decoration: a bar in the cursor's own color, at
    // the very bottom edge (underline shape) or very left edge (beam shape)
    // of the cell, reusing the font's underline thickness as the bar's
    // width -- independent of, and drawn after, the text-underline band
    // above so a cursor can sit on an underlined cell without conflict.
    // Block-shaped cursors don't reach this shader at all -- see app.py.
    if (in.cursor.w > 1.5) {
        let pixel_x = in.cell_u * u.cell_size.x;
        if (pixel_x <= u.underline.y) {
            rgb = in.cursor.rgb;
        }
    } else if (in.cursor.w > 0.5) {
        let pixel_y = in.cell_v * u.cell_size.y;
        if (pixel_y >= u.cell_size.y - u.underline.y) {
            rgb = in.cursor.rgb;
        }
    }
    return vec4<f32>(rgb, 1.0);
}
"""

# Must exactly match the WGSL Instance struct's field order/types/alignment.
INSTANCE_DTYPE = np.dtype(
    [
        ("col", "f4"),
        ("row", "f4"),
        ("atlas_col", "f4"),
        ("atlas_row", "f4"),
        ("fg", "f4", 4),
        ("bg", "f4", 4),
        ("flags", "f4", 4),
        ("cursor", "f4", 4),
    ]
)


_UNIFORM_DTYPE = np.dtype(
    [("screen_size", "f4", 2), ("cell_size", "f4", 2), ("atlas_grid", "f4", 2), ("underline", "f4", 2), ("_pad", "f4", 2)]
)


class CellRenderer:
    def __init__(self, gpu: GpuContext, atlas: GlyphAtlas, rows: int, cols: int, underline_y: float = 0.0, underline_thickness: float = 1.0) -> None:
        self.gpu = gpu
        self.atlas = atlas
        self.rows = rows
        self.cols = cols
        self._underline_y = underline_y
        self._underline_thickness = underline_thickness
        device = gpu.device

        shader = device.create_shader_module(code=_SHADER_SOURCE)

        self._atlas_texture = device.create_texture(
            size=(atlas.width, atlas.height, 1),
            format=wgpu.TextureFormat.r8unorm,
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
        )
        self._sampler = device.create_sampler()
        self._uniform_buffer = device.create_buffer(size=_UNIFORM_DTYPE.itemsize, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)

        self._bind_group_layout = device.create_bind_group_layout(
            entries=[
                {"binding": 0, "visibility": wgpu.ShaderStage.VERTEX, "buffer": {"type": wgpu.BufferBindingType.read_only_storage}},
                {"binding": 1, "visibility": wgpu.ShaderStage.VERTEX | wgpu.ShaderStage.FRAGMENT, "buffer": {"type": wgpu.BufferBindingType.uniform}},
                {"binding": 2, "visibility": wgpu.ShaderStage.FRAGMENT, "texture": {"sample_type": wgpu.TextureSampleType.float}},
                {"binding": 3, "visibility": wgpu.ShaderStage.FRAGMENT, "sampler": {"type": wgpu.SamplerBindingType.filtering}},
            ]
        )
        pipeline_layout = device.create_pipeline_layout(bind_group_layouts=[self._bind_group_layout])
        self._pipeline = device.create_render_pipeline(
            layout=pipeline_layout,
            vertex={"module": shader, "entry_point": "vs_main"},
            fragment={"module": shader, "entry_point": "fs_main", "targets": [{"format": gpu.format}]},
            primitive={"topology": wgpu.PrimitiveTopology.triangle_list},
        )
        self._instance_buffer = None
        self._bind_group = None
        self._allocate(rows, cols)
        self._upload_atlas_full()

    def _allocate(self, rows: int, cols: int) -> None:
        """(Re)creates the instance buffer sized for rows*cols, the bind
        group referencing it, and pushes the current screen_size/cell_size
        into the uniform buffer. Called from __init__ and from resize()."""
        device = self.gpu.device
        self._instance_buffer = device.create_buffer(
            size=max(1, rows * cols) * INSTANCE_DTYPE.itemsize,
            usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST,
        )
        uniforms = np.zeros(1, dtype=_UNIFORM_DTYPE)
        uniforms[0] = (
            (cols * self.atlas.cell_width, rows * self.atlas.cell_height),
            (self.atlas.cell_width, self.atlas.cell_height),
            (self.atlas.cols, self.atlas.rows),
            (self._underline_y, self._underline_thickness),
            (0, 0),
        )
        device.queue.write_buffer(self._uniform_buffer, 0, uniforms.tobytes())
        self._bind_group = device.create_bind_group(
            layout=self._bind_group_layout,
            entries=[
                {"binding": 0, "resource": {"buffer": self._instance_buffer, "offset": 0, "size": self._instance_buffer.size}},
                {"binding": 1, "resource": {"buffer": self._uniform_buffer, "offset": 0, "size": self._uniform_buffer.size}},
                {"binding": 2, "resource": self._atlas_texture.create_view()},
                {"binding": 3, "resource": self._sampler},
            ],
        )

    def resize(self, rows: int, cols: int) -> None:
        """Reallocates the instance buffer and updates screen_size for a new
        grid shape -- e.g. after a real window resize (see app.py's
        framebuffer-resize handler). A real, confirmed bug this fixes: before
        this existed, the grid was drawn using whatever rows/cols the window
        opened with even after the *actual* surface (and PTY/Screen) resized
        underneath it, stretching the fixed-size grid to fill a differently
        sized surface -- niri in particular never honors an app's requested
        initial window size (it sizes new windows from its own
        default-column-width policy instead), so this wasn't a rare edge
        case, it fired on every single launch."""
        if rows == self.rows and cols == self.cols:
            return
        self.rows, self.cols = rows, cols
        self._allocate(rows, cols)

    def _upload_atlas_full(self) -> None:
        self.gpu.device.queue.write_texture(
            {"texture": self._atlas_texture},
            self.atlas.image.tobytes(),
            {"bytes_per_row": self.atlas.width},
            (self.atlas.width, self.atlas.height, 1),
        )
        self.atlas.clear_dirty()

    def sync_atlas(self) -> None:
        """Uploads only the atlas's dirty region since the last sync, if any."""
        if self.atlas.dirty_rect is None:
            return
        x, y, w, h = self.atlas.dirty_rect
        region = np.ascontiguousarray(self.atlas.image[y:y + h, x:x + w])
        self.gpu.device.queue.write_texture(
            {"texture": self._atlas_texture, "origin": (x, y, 0)},
            region.tobytes(),
            {"bytes_per_row": w},
            (w, h, 1),
        )
        self.atlas.clear_dirty()

    def render(self, instances: np.ndarray) -> None:
        """instances: a structured array with dtype INSTANCE_DTYPE, length
        <= rows*cols. Uploads any pending atlas changes, updates the instance
        buffer, and issues one draw call.
        """
        self.sync_atlas()
        device = self.gpu.device
        device.queue.write_buffer(self._instance_buffer, 0, instances.tobytes())
        texture_view = self.gpu.context.get_current_texture().create_view()
        encoder = device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": texture_view,
                    "resolve_target": None,
                    "clear_value": (0, 0, 0, 1),
                    "load_op": wgpu.LoadOp.clear,
                    "store_op": wgpu.StoreOp.store,
                }
            ]
        )
        pass_.set_pipeline(self._pipeline)
        pass_.set_bind_group(0, self._bind_group)
        pass_.draw(6, len(instances))
        pass_.end()
        device.queue.submit([encoder.finish()])

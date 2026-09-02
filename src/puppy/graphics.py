"""Kitty graphics protocol -- model layer only, no GPU compositing yet (that is a
separate, later pass -- see PROGRESS.md).

v2 scope (this pass) adds `a=p` (put: display an already-transmitted image,
with placement id `p=`, z-index `z=`, explicit cell span `c=`/`r=`, and pixel
cropping `x=`/`y=`/`w=`/`h=`), `a=d` (delete, filters `a`/`A`, `i`/`I` +
optional `p=`, `c`/`C`, `p`/`P`, `q`/`Q`, `x`/`X`, `y`/`Y`, `z`/`Z` -- see
`_handle_delete`'s docstring for the exact filter semantics and one real,
documented approximation), `a=q` (query: validates a full transmission and
responds OK/error without persisting the image), z-index (placements now
carry `z_index`, sorted at render time -- see `render/graphics_renderer.py`),
and command responses (`\\x1b_Gi=<id>[,p=<placement>];OK` or `;ERROR:<code>:
<message>`, gated by `q=`/quiet the same way kitty's own
`finish_command_response` is) for every action that produces one. Confirmed
against kitty's real `grman_handle_command`: `a=T`'s display step is *not*
separate code -- it reuses the exact same put logic as `a=p` with the
original transmit command's `p=`/`z=`/`c=`/`r=`/`x=`/`y=`/`w=`/`h=` keys, so
puppy's `_finalize` calls the same placement-building path `_handle_put` does
rather than duplicating it.

v1 scope covered `a=T`/`a=t` (transmit, with `T` also displaying) only, `t=d`
(direct transmission) only, and formats `f=24`/`f=32` (direct RGB/RGBA) plus
`f=100` (PNG, optionally `o=z` zlib-compressed either way -- see below). Not
implemented, each a real separate follow-up milestone: `a=c` (compose two
*already-existing* frames, distinct from the frame-loading compositing `a=f`
does -- see below), unicode-placeholder, file/shm transmission (`t=f`/`t=t`/
`t=s`), image-number addressing (`I=`, and delete's `n=`/`N=`/`r=`/`R=`
variants that key off it), sub-cell pixel offsets (`X=`/`Y=`), parent-relative
placements (`P=`/`Q=` on put). None of these are silently mis-handled --
each unsupported control key/action is either ignored (same "absent" default
kitty itself falls back to) or produces no response, per the inline comments
at each such branch below.

v3 scope (this pass) adds animation: `a=f` (transmit frame data -- a new
frame by default, or edit an existing one via `r=`; the transmitted rectangle
is composited onto a background canvas that is either a previous frame's data
(`c=`, 1-based, `c=1` is the root frame), the frame being edited itself
(`r=`), or a solid `Y=` RGBA color, default transparent black -- `X=1` selects
a plain overwrite instead of the default alpha blend) and `a=a` (animation
control: `c=` jumps to a specific frame client-side, `s=` starts/stops
terminal-driven playback -- `1` stop, `2` run-but-wait-for-more-frames at the
end instead of looping, `3` run-and-loop -- `v=` sets the loop count, `1` =
infinite, and `r=`+`z=` sets an existing frame's gap, the only way to give the
root frame a nonzero gap since it has none by construction). Confirmed against
kitty's real `handle_animation_frame_load_command`/`handle_animation_control_
command`/`scan_active_animations`: frame numbers are 1-based and uniform
across root+extra frames (`frame_for_number`: `1` is root, `2+` indexes
`extra_frames`), a frame's gap floors negative ("gapless") values to `0`
(`change_gap`'s `MAX(0, gap)`), terminal-driven playback only actually
advances when `animation_state != STOPPED` *and* the image has at least one
extra frame *and* total gap across all its frames is nonzero (kitty's
`image_is_animatable`'s `animation_duration` check -- an animation whose
frames are all gapless would otherwise busy-loop forever advancing with no
visible effect), gapless frames are skipped over within a single tick until
landing on one with a real gap, and `s=2` (loading) freezes on the last frame
rather than wrapping once no next frame exists yet, correctly resuming once
the client transmits one. One deliberate simplification, not a bug: every
frame is stored fully composited (image-width*height RGBA8), not kitty's
lazy `base_frame_id` reference-chain representation -- this project already
established "don't pre-optimize, real GPU/parser draw-call counts are fine as
direct per-item work" as its standing performance philosophy (see `puppy.
render.graphics_renderer`'s own module docstring), and real terminal
animations (a spinner, a small sprite) are tiny compared to the reference
chain's actual purpose (deep multi-hundred-frame video-like animations kitty
itself only special-cases past a length threshold, see `reference_chain_too_
large`). `GraphicsManager.tick(now)` (a wall-clock `time.monotonic()`/`time.
time()`-style float, matching `render/app.py`'s existing cursor-blink clock)
must be called once per render tick by that layer -- it owns nothing about
*drawing* a frame, only *which* frame is current, matching this module's
"model layer only" scope. `render/graphics_renderer.py` renders whichever
frame `Image.current_frame_index` currently points to.

Confirmed against kitty's real `~/Projects/kitty/kitty/graphics.c`
(`grman_handle_command`, `handle_add_command`, `load_image_data`,
`INIT_CHUNKED_LOAD`): chunked transmission (`m=1` on every chunk but the last)
accumulates payload bytes across multiple APC commands into one in-progress load
-- a continuation chunk only carries `m` and the payload, not the original control
keys, so the pending load (not the image id) is what identifies where the bytes
go. Naming (`GraphicsManager`, `grman_*`-flavored method) matches kitty's own
vocabulary on purpose, for anyone cross-referencing its source later -- not
attempting a 1:1 port otherwise.

Sizing/DoS note: unlike OSC (capped to a few KB, see Parser._MAX_OSC_LEN), image
payloads are legitimately large, so the cap here is kitty's own real one for this
exact case -- direct, uncompressed RGB/RGBA transmission has an *exact* expected
size computed up front from the declared width*height*bpp (confirmed via
load_image_data's EFBIG check), so a chunked stream can't be flooded past it;
MAX_IMAGE_DIMENSION bounds the declared dimensions themselves before that
multiplication happens. PNG and `o=z`-compressed payloads have no such upfront
exact size (compressed size varies), so kitty instead caps raw accumulated bytes
at a real, much larger constant -- `MAX_DATA_SZ` below, confirmed against
graphics.c's own `#define MAX_DATA_SZ (4u * 100000000u)` -- and only validates
the *decompressed*/decoded size afterward.

PNG decoding (`f=100`): kitty links real libpng (`png-reader.c`); the direct
Python equivalent, matching this project's established pattern of using real
proven libraries for infrastructure rather than hand-rolling (HarfBuzz, FreeType,
wgpu-native are the same call elsewhere), is Pillow. Confirmed against
`inflate_png_inner`: libpng normalizes every color type/bit depth to RGBA8
(`png_set_palette_to_rgb`/`png_set_expand_gray_1_2_4_to_8`/`png_set_strip_16`/
`png_set_gray_to_rgb`/`png_set_filler` for the no-alpha case) -- Pillow's
`Image.convert("RGBA")` does the same normalization. Also confirmed: the PNG's
*own* header width/height (`png_get_image_width`/`_height`) become the image's
real dimensions, overriding whatever `s`/`v` the client declared (`load_data->
width = d.width` in `inflate_png`, unconditionally) -- kitty doesn't even require
`s`/`v` to be sent for `f=100`, and neither does puppy. Deliberately out of scope,
matching kitty's own optional extras rather than its mandatory decode path: ICC
colour-profile transforms and embedded-gamma correction (kitty's PNG path pulls
in `lcms2` for this; puppy's images are always treated as already-sRGB, which is
the common case and consistent with every other color path in this codebase).
"""
from __future__ import annotations

import base64
import io
import time
import zlib
from dataclasses import dataclass, field

import numpy as np
from PIL import Image as _PILImage

# Confirmed via kitty's real MAX_IMAGE_DIMENSION (graphics.c).
MAX_IMAGE_DIMENSION = 10000

# Confirmed via kitty's real MAX_DATA_SZ (graphics.c): `4u * 100000000u`. Caps
# raw accumulated bytes for PNG/compressed loads, which have no exact upfront
# expected size the way uncompressed direct RGB/RGBA does.
MAX_DATA_SZ = 4 * 100_000_000


@dataclass
class Frame:
    """One animation frame beyond the root -- always stored fully composited
    as image-width*height RGBA8, regardless of what format/rectangle it was
    originally transmitted in. See the module docstring's v3 scope note for
    why (a deliberate simplification vs. kitty's lazy reference-chain
    storage)."""
    data: bytes
    gap: int = 40  # ms; kitty's real DEFAULT_GAP for a newly created frame


@dataclass
class Image:
    id: int
    width: int
    height: int
    format: int  # 24 (RGB) or 32 (RGBA)
    data: bytes  # the root frame's pixel data
    frames: list[Frame] = field(default_factory=list)  # extra frames, 1-based frame number 2+
    root_gap: int = 0  # the root frame has no transmitted gap; only a=a's r=1,z= can set one
    # 0 = root frame is current; N (1..len(frames)) = frames[N-1] is current --
    # matches kitty's own current_frame_index convention exactly.
    current_frame_index: int = 0
    animation_state: int = 0  # 0 stopped, 1 loading (run but freeze at the end), 2 running (loop)
    max_loops: int = 0  # 0 = infinite; kitty's own "loops - 1" encoding, see _handle_animation_control
    current_loop: int = 0
    frame_shown_at: float = 0.0  # wall-clock time.monotonic()-style, set by tick()/_handle_animation_control


@dataclass
class Placement:
    image_id: int
    row: int
    col: int
    placement_id: int = 0
    z_index: int = 0
    num_cols: int = 0  # 0 = auto: ceil(image.width / cell_width), resolved at render time
    num_rows: int = 0  # 0 = auto: ceil(image.height / cell_height), resolved at render time
    src_x: int = 0
    src_y: int = 0
    src_width: int = 0  # 0 = auto: image.width - src_x, resolved at render time
    src_height: int = 0  # 0 = auto: image.height - src_y, resolved at render time


@dataclass
class _PendingLoad:
    image_id: int
    format: int  # 24, 32, or 100 (PNG) -- 100 always resolves to a 32 Image on completion
    width: int  # 0 for PNG until decoded; the real header size then overrides this
    height: int
    compressed: bool  # o=z
    # Exact byte count for uncompressed direct RGB/RGBA (width*height*bpp, known
    # upfront); None for PNG/compressed loads, which have no exact expected size
    # until decompressed/decoded -- MAX_DATA_SZ caps accumulation instead.
    expected_size: int | None
    display: bool  # True for a=T, False for a=t/a=q (no placement)
    is_query: bool  # True for a=q -- validates but never persists into self.images
    quiet: int  # q= : 0 always respond, 1 suppress OK only, 2 suppress everything
    cursor_row: int
    cursor_col: int
    # Put-command params carried by the *transmit* command itself -- a=T's
    # display step reuses these via _place(), exactly like kitty's own
    # handle_put_command call out of handle_add_command.
    placement_id: int = 0
    z_index: int = 0
    num_cols: int = 0
    num_rows: int = 0
    src_x: int = 0
    src_y: int = 0
    src_width: int = 0
    src_height: int = 0
    buf: bytearray = field(default_factory=bytearray)
    # a=f (animation frame) load only -- see _start_animation_frame_load/
    # _finalize_animation_frame. Unused (defaults) for every other load kind.
    is_animation_frame: bool = False
    anim_frame_ref: int = 0  # r=: 1-based frame being edited; 0 = create a new frame
    anim_base_ref: int = 0  # c=: 1-based frame providing base canvas data for a *new* frame
    anim_gap: int = 0  # z=: raw value, resolved (default/gapless-floor) at finalize time
    anim_compose_overwrite: bool = False  # X=1: plain replace instead of the default alpha blend
    anim_bgcolor: int = 0  # Y=: 32-bit RGBA background canvas color when no c=/r= base is given
    anim_dst_x: int = 0  # x=: left edge of the transmitted rectangle within the frame canvas
    anim_dst_y: int = 0  # y=: top edge of the transmitted rectangle within the frame canvas


class GraphicsManager:
    def __init__(self) -> None:
        self.images: dict[int, Image] = {}
        self.placements: list[Placement] = []
        self._loading: _PendingLoad | None = None

    def handle_command(self, control: dict[str, str], payload_b64: bytes, cursor_row: int, cursor_col: int) -> bytes | None:
        if self._loading is not None:
            # Continuation chunk: only 'm' and the payload are meaningful, per
            # kitty's real INIT_CHUNKED_LOAD (the original control keys aren't
            # re-sent and shouldn't be re-read).
            return self._append_chunk(payload_b64, control.get("m", "0") == "1")

        # Action absent behaves like 't' (transmit only) -- confirmed via
        # grman_handle_command's `case 0: case 't': case 'T': case 'q':` grouping
        # 0/absent and 't' into the same branch.
        action = control.get("a", "t")

        if action == "d":
            self._handle_delete(control, cursor_row, cursor_col)
            return None  # kitty's own handle_delete_command never produces a response

        if action == "p":
            return self._handle_put(control, cursor_row, cursor_col)

        if action == "a":
            return self._handle_animation_control(control)

        if action == "f":
            return self._start_animation_frame_load(control, payload_b64, cursor_row, cursor_col)

        if action not in ("t", "T", "q"):
            return None  # a=c (compose) -- out of scope, see module docstring

        if control.get("t", "d") != "d":
            return None  # file/shm transmission -- out of scope

        fmt = self._int(control.get("f", "32"))
        if fmt not in (24, 32, 100):
            return None  # unknown format -- out of scope

        compressed_spec = control.get("o", "")
        if compressed_spec not in ("", "z"):
            return None  # unknown compression -- out of scope
        compressed = compressed_spec == "z"

        image_id = self._int(control.get("i", "0"))
        is_query = action == "q"
        if is_query and not image_id:
            return None  # kitty: query without an id is a terminal-side error report only, no client response

        if fmt == 100:
            # PNG: s/v are not required and, per kitty's real inflate_png (see
            # module docstring), not even consulted -- the PNG's own header
            # dimensions win once decoded. No exact expected_size upfront.
            width = height = 0
            expected_size = None
        else:
            width = self._int(control.get("s", "0"))
            height = self._int(control.get("v", "0"))
            if not width or not height:
                return None
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                return None
            bpp = fmt // 8
            # Exact size is only knowable upfront for uncompressed direct
            # RGB/RGBA -- compressed payload bytes are smaller than that by
            # construction, so the exact-match cap would reject every real
            # compressed transmission; fall back to the MAX_DATA_SZ cap and
            # validate the decompressed length afterward instead.
            expected_size = None if compressed else width * height * bpp

        self._loading = _PendingLoad(
            image_id=image_id,
            format=fmt,
            width=width,
            height=height,
            compressed=compressed,
            expected_size=expected_size,
            display=action == "T",
            is_query=is_query,
            quiet=self._int(control.get("q", "0")),
            cursor_row=cursor_row,
            cursor_col=cursor_col,
            placement_id=self._int(control.get("p", "0")),
            z_index=self._int(control.get("z", "0")),
            num_cols=self._int(control.get("c", "0")),
            num_rows=self._int(control.get("r", "0")),
            src_x=self._int(control.get("x", "0")),
            src_y=self._int(control.get("y", "0")),
            src_width=self._int(control.get("w", "0")),
            src_height=self._int(control.get("h", "0")),
        )
        return self._append_chunk(payload_b64, control.get("m", "0") == "1")

    @staticmethod
    def _int(s: str) -> int:
        try:
            return int(s)
        except (TypeError, ValueError):
            return 0

    def _append_chunk(self, payload_b64: bytes, more: bool) -> bytes | None:
        load = self._loading
        assert load is not None
        try:
            chunk = base64.b64decode(payload_b64, validate=False)
        except Exception:
            self._loading = None
            return self._response(load, ok=False, code="EINVAL", message="Failed to decode base64 payload")
        cap = load.expected_size if load.expected_size is not None else MAX_DATA_SZ
        if len(load.buf) + len(chunk) > cap:
            # Same real protection as kitty's own EFBIG check in load_image_data:
            # uncompressed direct RGB/RGBA has an exact expected size so any
            # overflow is rejected outright; PNG/compressed loads have no exact
            # size but still can't grow past kitty's real MAX_DATA_SZ ceiling.
            self._loading = None
            return self._response(load, ok=False, code="EFBIG", message="Image data too large")
        load.buf.extend(chunk)
        if more:
            return None  # more chunks to come, stay in the loading state
        self._loading = None
        return self._finalize(load)

    def _finalize(self, load: "_PendingLoad") -> bytes | None:
        raw = bytes(load.buf)
        width, height, fmt = load.width, load.height, load.format

        if load.compressed:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                return self._response(load, ok=False, code="EINVAL", message="Failed to decompress image data")

        if fmt == 100:
            decoded = self._decode_png(raw)
            if decoded is None:
                return self._response(load, ok=False, code="EINVAL", message="Failed to decode PNG data")
            width, height, raw = decoded
            fmt = 32  # libpng (and Pillow's convert("RGBA") equivalent) always yields RGBA8
        else:
            bpp = fmt // 8
            if len(raw) != width * height * bpp:
                # short/oversized transmission -- discard rather than storing a bad image
                return self._response(
                    load, ok=False, code="EINVAL",
                    message=f"Image dimensions {width}x{height} do not match data size {len(raw)}",
                )

        if load.is_animation_frame:
            return self._finalize_animation_frame(load, width, height, fmt, raw)

        # a=q (query) validates the full transmission but never persists it,
        # matching kitty's real grman_handle_command (`if (g->action == 'q')
        # remove_images(...)` right after responding).
        if not load.is_query:
            self.images[load.image_id] = Image(id=load.image_id, width=width, height=height, format=fmt, data=raw)
            if load.display:
                self._place(
                    image_id=load.image_id, row=load.cursor_row, col=load.cursor_col,
                    placement_id=load.placement_id, z_index=load.z_index,
                    num_cols=load.num_cols, num_rows=load.num_rows,
                    src_x=load.src_x, src_y=load.src_y, src_width=load.src_width, src_height=load.src_height,
                )
        return self._response(load, ok=True)

    def _start_animation_frame_load(self, control: dict[str, str], payload_b64: bytes, cursor_row: int, cursor_col: int) -> bytes | None:
        image_id = self._int(control.get("i", "0"))
        quiet = self._int(control.get("q", "0"))
        image = self.images.get(image_id)
        if image is None:
            return self._raw_response(
                image_id, 0, quiet, ok=False, code="ENOENT",
                message=f"Animation command refers to non-existent image with id: {image_id}",
            )

        if control.get("t", "d") != "d":
            return None  # file/shm transmission -- out of scope, same as regular transmit

        fmt = self._int(control.get("f", "32"))
        if fmt not in (24, 32, 100):
            return None
        compressed_spec = control.get("o", "")
        if compressed_spec not in ("", "z"):
            return None
        compressed = compressed_spec == "z"

        if fmt == 100:
            width = height = 0
            expected_size = None
        else:
            # Unlike a regular transmit, s=/v= default to the *image's* own
            # size rather than being required -- a full-frame transmission
            # (the common case) doesn't need to repeat them, confirmed
            # against the real spec's "the escape codes used are exactly the
            # same as for transferring image data" full-frame example, which
            # never sends s=/v= at all.
            width = self._int(control.get("s", "0")) or image.width
            height = self._int(control.get("v", "0")) or image.height
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                return None
            bpp = fmt // 8
            expected_size = None if compressed else width * height * bpp

        self._loading = _PendingLoad(
            image_id=image_id, format=fmt, width=width, height=height,
            compressed=compressed, expected_size=expected_size,
            display=False, is_query=False, quiet=quiet,
            cursor_row=cursor_row, cursor_col=cursor_col,
            is_animation_frame=True,
            anim_frame_ref=self._int(control.get("r", "0")),
            anim_base_ref=self._int(control.get("c", "0")),
            anim_gap=self._int(control.get("z", "0")),
            anim_compose_overwrite=self._int(control.get("X", "0")) == 1,
            anim_bgcolor=self._int(control.get("Y", "0")),
            anim_dst_x=self._int(control.get("x", "0")),
            anim_dst_y=self._int(control.get("y", "0")),
        )
        return self._append_chunk(payload_b64, control.get("m", "0") == "1")

    def _finalize_animation_frame(self, load: "_PendingLoad", rect_width: int, rect_height: int, fmt: int, raw: bytes) -> bytes | None:
        image = self.images.get(load.image_id)
        if image is None:
            # Real, if unlikely, race: the image was deleted while this
            # (possibly chunked) frame transmission was still in flight.
            return self._response(load, ok=False, code="ENOENT", message=f"Animation command refers to non-existent image with id: {load.image_id}")
        if rect_width > image.width or rect_height > image.height:
            return self._response(
                load, ok=False, code="EINVAL",
                message=f"Frame dimensions {rect_width}x{rect_height} larger than image {image.width}x{image.height}",
            )

        total_frames = 1 + len(image.frames)
        frame_ref = load.anim_frame_ref  # r=: edit this existing frame; 0 = create a new one
        base_ref = load.anim_base_ref  # c=: base canvas for a *new* frame, 0 = use anim_bgcolor instead
        if frame_ref and frame_ref > total_frames:
            return self._response(load, ok=False, code="EINVAL", message=f"No frame with number: {frame_ref} found")
        if base_ref and base_ref > total_frames:
            return self._response(load, ok=False, code="EINVAL", message=f"No frame with number: {base_ref} found")

        rect_rgba = self._as_rgba_array(raw, rect_width, rect_height, fmt)
        if frame_ref:
            canvas = self._frame_array(image, frame_ref).copy()
        elif base_ref:
            canvas = self._frame_array(image, base_ref).copy()
        else:
            canvas = self._solid_canvas_array(image.width, image.height, load.anim_bgcolor)
        self._composite(canvas, rect_rgba, load.anim_dst_x, load.anim_dst_y, overwrite=load.anim_compose_overwrite)
        composited = np.ascontiguousarray(canvas).tobytes()

        if frame_ref:
            self._set_frame_data(image, frame_ref, composited)
            if load.anim_gap:
                self._set_frame_gap(image, frame_ref, load.anim_gap)
        else:
            gap = load.anim_gap
            resolved_gap = 0 if gap < 0 else (gap if gap > 0 else 40)  # 40 == kitty's real DEFAULT_GAP
            image.frames.append(Frame(data=composited, gap=resolved_gap))

        return self._response(load, ok=True)

    def _handle_animation_control(self, control: dict[str, str]) -> bytes | None:
        image_id = self._int(control.get("i", "0"))
        quiet = self._int(control.get("q", "0"))
        image = self.images.get(image_id)
        if image is None:
            return self._raw_response(
                image_id, 0, quiet, ok=False, code="ENOENT",
                message=f"Animation command refers to non-existent image with id: {image_id}",
            )
        total_frames = 1 + len(image.frames)

        # r= + z=: set an existing frame's gap -- independent of the other
        # keys below, confirmed against kitty's real handle_animation_control_
        # command (its own separate `if (g->frame_number)` block).
        frame_ref = self._int(control.get("r", "0"))
        gap = self._int(control.get("z", "0"))
        if frame_ref and gap and frame_ref <= total_frames:
            self._set_frame_gap(image, frame_ref, gap)

        # c=: client-driven -- jump straight to a specific frame.
        jump_to = self._int(control.get("c", "0"))
        if jump_to and jump_to <= total_frames:
            image.current_frame_index = jump_to - 1
            image.frame_shown_at = time.monotonic()

        # s=: 1 stop, 2 run-but-freeze-at-the-end (wait for more frames), 3 run-and-loop.
        state = self._int(control.get("s", "0"))
        if state:
            old_state = image.animation_state
            image.animation_state = {1: 0, 2: 1, 3: 2}.get(state, image.animation_state)
            if image.animation_state != 0 and old_state == 0:
                image.frame_shown_at = time.monotonic()
            image.current_loop = 0  # kitty resets the loop counter on every explicit s=, any direction

        # v=: 0 ignored, 1 infinite (kitty's own "loops - 1" encoding, so max_loops stays 0 == infinite).
        loops = self._int(control.get("v", "0"))
        if loops:
            image.max_loops = loops - 1

        return self._raw_response(image_id, 0, quiet, ok=True)

    def tick(self, now: float) -> bool:
        """Advances every image whose animation is terminal-driven-running
        (a=a's s=2/s=3) per its frames' real gaps. Call once per render tick
        with a monotonic wall clock (render/app.py uses the same clock as its
        cursor-blink timing) -- this owns only *which* frame is current, not
        drawing it (render/graphics_renderer.py does that). Returns True if
        any image's current frame changed, so the caller knows a redraw
        would show something new (though in practice this project's render
        loop redraws unconditionally every tick anyway, see app.py)."""
        dirtied = False
        for image in self.images.values():
            if image.animation_state == 0 or not image.frames:
                continue
            total_frames = 1 + len(image.frames)
            # kitty's own is_animatable gate: an animation whose frames are
            # *all* gapless would otherwise spin forever advancing with no
            # visible effect (and no way to ever stop, short of a=a s=1).
            total_gap = image.root_gap + sum(f.gap for f in image.frames)
            if total_gap == 0:
                continue
            gap = self._frame_gap(image, image.current_frame_index + 1)
            if now < image.frame_shown_at + gap / 1000.0:
                continue
            while True:
                nxt = (image.current_frame_index + 1) % total_frames
                if nxt == 0:
                    if image.animation_state == 1:  # loading: wait for more frames rather than loop
                        break
                    image.current_loop += 1
                    if image.max_loops and image.current_loop >= image.max_loops:
                        break  # finished: freeze on the last frame shown
                image.current_frame_index = nxt
                dirtied = True
                image.frame_shown_at = now
                if self._frame_gap(image, nxt + 1) != 0:
                    break  # landed on a real (non-gapless) frame -- stop advancing for this tick
        return dirtied

    @staticmethod
    def _as_rgba_array(raw: bytes, width: int, height: int, fmt: int) -> np.ndarray:
        channels = fmt // 8
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, channels)
        if channels == 3:
            alpha = np.full((height, width, 1), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=2)
        return arr

    @staticmethod
    def _frame_array(image: "Image", frame_number: int) -> np.ndarray:
        if frame_number <= 1:
            return GraphicsManager._as_rgba_array(image.data, image.width, image.height, image.format)
        frame = image.frames[frame_number - 2]
        return GraphicsManager._as_rgba_array(frame.data, image.width, image.height, 32)

    @staticmethod
    def _set_frame_data(image: "Image", frame_number: int, data: bytes) -> None:
        if frame_number <= 1:
            image.data = data
            image.format = 32  # composited frames are always RGBA
        else:
            image.frames[frame_number - 2].data = data

    @staticmethod
    def _set_frame_gap(image: "Image", frame_number: int, gap: int) -> None:
        resolved = max(0, gap)  # negative == gapless, floored at 0, matches kitty's change_gap
        if frame_number <= 1:
            image.root_gap = resolved
        else:
            image.frames[frame_number - 2].gap = resolved

    @staticmethod
    def _frame_gap(image: "Image", frame_number: int) -> int:
        if frame_number <= 1:
            return image.root_gap
        return image.frames[frame_number - 2].gap

    @staticmethod
    def _solid_canvas_array(width: int, height: int, bgcolor: int) -> np.ndarray:
        # 32-bit RGBA packed big-endian within the value, confirmed against
        # the spec's own example (Y=4278190335 == 0xff0000ff == opaque red).
        rgba = np.array([(bgcolor >> 24) & 0xFF, (bgcolor >> 16) & 0xFF, (bgcolor >> 8) & 0xFF, bgcolor & 0xFF], dtype=np.uint8)
        return np.tile(rgba, (height, width, 1))

    @staticmethod
    def _composite(canvas: np.ndarray, rect: np.ndarray, dst_x: int, dst_y: int, *, overwrite: bool) -> None:
        """In-place straight-alpha "over" compositing of `rect` onto `canvas`
        at (dst_x, dst_y), clipped to canvas bounds -- same formula as
        kitty's real alpha_blend() in graphics.c. overwrite=True (X=1) does a
        plain replace instead, matching the spec's "simple replacement"
        composition mode."""
        canvas_h, canvas_w = canvas.shape[:2]
        rect_h, rect_w = rect.shape[:2]
        src_x0, src_y0 = max(0, -dst_x), max(0, -dst_y)
        dst_x0, dst_y0 = max(0, dst_x), max(0, dst_y)
        w = min(rect_w - src_x0, canvas_w - dst_x0)
        h = min(rect_h - src_y0, canvas_h - dst_y0)
        if w <= 0 or h <= 0:
            return
        src = rect[src_y0:src_y0 + h, src_x0:src_x0 + w].astype(np.float32)
        dst_slice = canvas[dst_y0:dst_y0 + h, dst_x0:dst_x0 + w]
        if overwrite:
            dst_slice[:] = src.astype(np.uint8)
            return
        dst = dst_slice.astype(np.float32)
        src_a = src[..., 3:4] / 255.0
        dst_a = dst[..., 3:4] / 255.0
        out_a = src_a + dst_a * (1.0 - src_a)
        safe_a = np.where(out_a == 0, 1.0, out_a)
        out_rgb = (src[..., :3] * src_a + dst[..., :3] * dst_a * (1.0 - src_a)) / safe_a
        out = np.concatenate([out_rgb, out_a * 255.0], axis=2)
        out = np.where(out_a == 0, 0.0, out)
        dst_slice[:] = np.clip(out, 0, 255).astype(np.uint8)

    def _place(self, *, image_id: int, row: int, col: int, placement_id: int, z_index: int,
               num_cols: int, num_rows: int, src_x: int, src_y: int, src_width: int, src_height: int) -> None:
        # A repeated put with the same placement id updates that placement in
        # place rather than adding a second one -- confirmed against kitty's
        # real handle_put_command (`if (ref) ...` reuses the found ref instead
        # of calling create_ref).
        if placement_id:
            self.placements = [
                pl for pl in self.placements if not (pl.image_id == image_id and pl.placement_id == placement_id)
            ]
        self.placements.append(Placement(
            image_id=image_id, row=row, col=col, placement_id=placement_id, z_index=z_index,
            num_cols=num_cols, num_rows=num_rows, src_x=src_x, src_y=src_y, src_width=src_width, src_height=src_height,
        ))

    def _handle_put(self, control: dict[str, str], cursor_row: int, cursor_col: int) -> bytes | None:
        quiet = self._int(control.get("q", "0"))
        image_id = self._int(control.get("i", "0"))
        placement_id = self._int(control.get("p", "0"))
        if not image_id:
            # Image-number-only addressing (`I=`) isn't tracked -- see module docstring.
            return self._raw_response(image_id, placement_id, quiet, ok=False, code="ENOENT", message="Put command without an image id")
        image = self.images.get(image_id)
        if image is None:
            return self._raw_response(
                image_id, placement_id, quiet, ok=False, code="ENOENT",
                message=f"Put command refers to non-existent image with id: {image_id}",
            )
        self._place(
            image_id=image_id, row=cursor_row, col=cursor_col, placement_id=placement_id,
            z_index=self._int(control.get("z", "0")),
            num_cols=self._int(control.get("c", "0")), num_rows=self._int(control.get("r", "0")),
            src_x=self._int(control.get("x", "0")), src_y=self._int(control.get("y", "0")),
            src_width=self._int(control.get("w", "0")), src_height=self._int(control.get("h", "0")),
        )
        return self._raw_response(image_id, placement_id, quiet, ok=True)

    def _handle_delete(self, control: dict[str, str], cursor_row: int, cursor_col: int) -> None:
        """Delete filters, confirmed against kitty's real handle_delete_command
        filter functions (x_filter_func/y_filter_func/z_filter_func/
        point_filter_func/point3d_filter_func): lowercase frees placements
        only, uppercase (`A`/`I`/`P`/`Q`/`X`/`Y`/`Z`/`C`) also frees the
        underlying image data once no placement references it. `n`/`N`
        (by image *number*), `r`/`R` (id range), and `f`/`F` (animation frame)
        are not supported -- see module docstring.

        One real, documented approximation: kitty's point/column/row/z-index
        filters test against a placement's *actual rendered* cell span, which
        depends on cell pixel dimensions this model layer doesn't have (only
        the render layer does). An explicit `c=`/`r=` on the placement is
        still matched exactly; an auto-sized placement (`c=`/`r=` never given)
        is approximated as covering only its anchor cell.
        """
        spec = control.get("d", "a") or "a"
        action = spec.lower()
        free_data = spec.isupper()

        if action == "a":
            self.placements.clear()
            if free_data:
                self.images.clear()
            return

        if action == "i":
            image_id = self._int(control.get("i", "0"))
            placement_id = self._int(control.get("p", "0"))
            if placement_id:
                keep = lambda pl: not (pl.image_id == image_id and pl.placement_id == placement_id)  # noqa: E731
            else:
                keep = lambda pl: pl.image_id != image_id  # noqa: E731
            self.placements = [pl for pl in self.placements if keep(pl)]
            if free_data and not any(pl.image_id == image_id for pl in self.placements):
                self.images.pop(image_id, None)
            return

        if action in ("n", "r", "f"):
            return  # by image number / id range / animation frame -- not supported, see module docstring

        if action == "c":
            keep = lambda pl: not self._covers(pl, cursor_row, cursor_col)  # noqa: E731
        elif action == "p":
            col = self._int(control.get("x", "0")) - 1
            row = self._int(control.get("y", "0")) - 1
            keep = lambda pl: not self._covers(pl, row, col)  # noqa: E731
        elif action == "q":
            col = self._int(control.get("x", "0")) - 1
            row = self._int(control.get("y", "0")) - 1
            z = self._int(control.get("z", "0"))
            keep = lambda pl: not (pl.z_index == z and self._covers(pl, row, col))  # noqa: E731
        elif action == "x":
            col = self._int(control.get("x", "0")) - 1
            keep = lambda pl: not (pl.col <= col < pl.col + (pl.num_cols or 1))  # noqa: E731
        elif action == "y":
            row = self._int(control.get("y", "0")) - 1
            keep = lambda pl: not (pl.row <= row < pl.row + (pl.num_rows or 1))  # noqa: E731
        elif action == "z":
            z = self._int(control.get("z", "0"))
            keep = lambda pl: pl.z_index != z  # noqa: E731
        else:
            return  # unrecognized delete action -- ignored, matches kitty's REPORT_ERROR-then-continue

        removed_ids = {pl.image_id for pl in self.placements if not keep(pl)}
        self.placements = [pl for pl in self.placements if keep(pl)]
        if free_data:
            remaining_ids = {pl.image_id for pl in self.placements}
            for image_id in removed_ids - remaining_ids:
                self.images.pop(image_id, None)

    @staticmethod
    def _covers(pl: "Placement", row: int, col: int) -> bool:
        num_cols = pl.num_cols or 1
        num_rows = pl.num_rows or 1
        return pl.row <= row < pl.row + num_rows and pl.col <= col < pl.col + num_cols

    def _response(self, load: "_PendingLoad", *, ok: bool, code: str = "", message: str = "") -> bytes | None:
        return self._raw_response(load.image_id, load.placement_id, load.quiet, ok, code, message)

    @staticmethod
    def _raw_response(image_id: int, placement_id: int, quiet: int, ok: bool, code: str = "", message: str = "") -> bytes | None:
        # Format and quiet gating confirmed against kitty's real
        # finish_command_response: no response at all without an image id;
        # q=1 suppresses only the OK case, q=2 suppresses every response.
        if not image_id:
            return None
        if quiet >= 1 and ok:
            return None
        if quiet >= 2:
            return None
        parts = [f"i={image_id}"]
        if placement_id:
            parts.append(f"p={placement_id}")
        status = "OK" if ok else f"{code}:{message}"
        body = "G" + ",".join(parts) + ";" + status
        return b"\x1b_" + body.encode("ascii") + b"\x1b\\"

    @staticmethod
    def _decode_png(data: bytes) -> tuple[int, int, bytes] | None:
        try:
            with _PILImage.open(io.BytesIO(data)) as im:
                im = im.convert("RGBA")
                width, height = im.size
                if not width or not height:
                    return None
                if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                    return None  # confirmed via kitty's inflate_png_inner, same cap it applies post-decode
                return width, height, im.tobytes()
        except Exception:
            return None

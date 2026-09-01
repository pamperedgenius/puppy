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
implemented, each a real separate follow-up milestone: `a=a`/`a=f`
(animation), `a=c` (compose), unicode-placeholder, file/shm transmission
(`t=f`/`t=t`/`t=s`), image-number addressing (`I=`, and delete's `n=`/`N=`/
`r=`/`R=` variants that key off it), sub-cell pixel offsets (`X=`/`Y=`),
parent-relative placements (`P=`/`Q=` on put). None of these are silently
mis-handled -- each unsupported control key/action is either ignored (same
"absent" default kitty itself falls back to) or produces no response, per the
inline comments at each such branch below.

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
import zlib
from dataclasses import dataclass, field

from PIL import Image as _PILImage

# Confirmed via kitty's real MAX_IMAGE_DIMENSION (graphics.c).
MAX_IMAGE_DIMENSION = 10000

# Confirmed via kitty's real MAX_DATA_SZ (graphics.c): `4u * 100000000u`. Caps
# raw accumulated bytes for PNG/compressed loads, which have no exact upfront
# expected size the way uncompressed direct RGB/RGBA does.
MAX_DATA_SZ = 4 * 100_000_000


@dataclass
class Image:
    id: int
    width: int
    height: int
    format: int  # 24 (RGB) or 32 (RGBA)
    data: bytes


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

        if action not in ("t", "T", "q"):
            return None  # a=a/a=f (animation), a=c (compose) -- out of scope, see module docstring

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

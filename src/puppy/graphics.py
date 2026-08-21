"""Kitty graphics protocol -- model layer only, no GPU compositing yet (that is a
separate, later pass -- see PROGRESS.md).

v1 scope covers `a=T`/`a=t` (transmit, with `T` also displaying) only, `t=d`
(direct transmission) only, and formats `f=24`/`f=32` (direct RGB/RGBA) plus
`f=100` (PNG, optionally `o=z` zlib-compressed either way -- see below). Not
implemented, each a real separate follow-up milestone: `a=p` (put/display-only),
`a=d` (delete), `a=q` (query), `a=a`/`a=f` (animation), `a=c` (compose),
unicode-placeholder, file/shm transmission (`t=f`/`t=t`/`t=s`).

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
    display: bool  # True for a=T, False for a=t (transmit only, no placement)
    cursor_row: int
    cursor_col: int
    buf: bytearray = field(default_factory=bytearray)


class GraphicsManager:
    def __init__(self) -> None:
        self.images: dict[int, Image] = {}
        self.placements: list[Placement] = []
        self._loading: _PendingLoad | None = None

    def handle_command(self, control: dict[str, str], payload_b64: bytes, cursor_row: int, cursor_col: int) -> None:
        if self._loading is not None:
            # Continuation chunk: only 'm' and the payload are meaningful, per
            # kitty's real INIT_CHUNKED_LOAD (the original control keys aren't
            # re-sent and shouldn't be re-read).
            self._append_chunk(payload_b64, control.get("m", "0") == "1")
            return

        # Action absent behaves like 't' (transmit only) -- confirmed via
        # grman_handle_command's `case 0: case 't': case 'T': case 'q':` grouping
        # 0/absent and 't' into the same branch.
        action = control.get("a", "t")
        if action not in ("t", "T"):
            return  # a=p/a=d/a=q/animation/compose -- out of scope, see module docstring

        if control.get("t", "d") != "d":
            return  # file/shm transmission -- out of scope

        fmt = self._int(control.get("f", "32"))
        if fmt not in (24, 32, 100):
            return  # unknown format -- out of scope

        compressed_spec = control.get("o", "")
        if compressed_spec not in ("", "z"):
            return  # unknown compression -- out of scope
        compressed = compressed_spec == "z"

        image_id = self._int(control.get("i", "0"))

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
                return
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                return
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
            cursor_row=cursor_row,
            cursor_col=cursor_col,
        )
        self._append_chunk(payload_b64, control.get("m", "0") == "1")

    @staticmethod
    def _int(s: str) -> int:
        try:
            return int(s)
        except (TypeError, ValueError):
            return 0

    def _append_chunk(self, payload_b64: bytes, more: bool) -> None:
        load = self._loading
        assert load is not None
        try:
            chunk = base64.b64decode(payload_b64, validate=False)
        except Exception:
            self._loading = None
            return
        cap = load.expected_size if load.expected_size is not None else MAX_DATA_SZ
        if len(load.buf) + len(chunk) > cap:
            # Same real protection as kitty's own EFBIG check in load_image_data:
            # uncompressed direct RGB/RGBA has an exact expected size so any
            # overflow is rejected outright; PNG/compressed loads have no exact
            # size but still can't grow past kitty's real MAX_DATA_SZ ceiling.
            self._loading = None
            return
        load.buf.extend(chunk)
        if more:
            return  # more chunks to come, stay in the loading state
        self._loading = None
        self._finalize(load)

    def _finalize(self, load: "_PendingLoad") -> None:
        raw = bytes(load.buf)
        width, height, fmt = load.width, load.height, load.format

        if load.compressed:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                return  # corrupt/incomplete compressed data -- discard

        if fmt == 100:
            decoded = self._decode_png(raw)
            if decoded is None:
                return  # corrupt/incomplete PNG -- discard, matches kitty's own error->discard behavior
            width, height, raw = decoded
            fmt = 32  # libpng (and Pillow's convert("RGBA") equivalent) always yields RGBA8
        else:
            bpp = fmt // 8
            if len(raw) != width * height * bpp:
                return  # short/oversized transmission -- discard rather than storing a bad image

        self.images[load.image_id] = Image(id=load.image_id, width=width, height=height, format=fmt, data=raw)
        if load.display:
            self.placements.append(Placement(image_id=load.image_id, row=load.cursor_row, col=load.cursor_col))

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

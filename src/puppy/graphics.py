"""Kitty graphics protocol -- model layer only, no GPU compositing yet (that is a
separate, later pass -- see PROGRESS.md).

v1 scope, deliberately narrow: `a=T`/`a=t` (transmit, with `T` also displaying)
only, `f=24`/`f=32` (direct RGB/RGBA) only, `t=d` (direct transmission) only. Not
implemented, each a real separate follow-up milestone: `f=100` (PNG), compression
(`o=z`), `a=p` (put/display-only), `a=d` (delete), `a=q` (query), `a=a`/`a=f`
(animation), `a=c` (compose), unicode-placeholder, file/shm transmission
(`t=f`/`t=t`/`t=s`).

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
exact case -- direct RGB/RGBA transmission has an *exact* expected size computed
up front from the declared width*height*bpp (confirmed via load_image_data's
EFBIG check), so a chunked stream can't be flooded past it; MAX_IMAGE_DIMENSION
bounds the declared dimensions themselves before that multiplication happens.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

# Confirmed via kitty's real MAX_IMAGE_DIMENSION (graphics.c).
MAX_IMAGE_DIMENSION = 10000


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
    format: int
    width: int
    height: int
    expected_size: int
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
        if fmt not in (24, 32):
            return  # PNG (100) or unknown -- out of scope

        image_id = self._int(control.get("i", "0"))
        width = self._int(control.get("s", "0"))
        height = self._int(control.get("v", "0"))
        if not width or not height:
            return
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            return

        bpp = fmt // 8
        expected_size = width * height * bpp

        self._loading = _PendingLoad(
            image_id=image_id,
            format=fmt,
            width=width,
            height=height,
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
        if len(load.buf) + len(chunk) > load.expected_size:
            # Same real protection as kitty's own EFBIG check in load_image_data:
            # direct RGB/RGBA has an exact expected size, so a chunked stream
            # can't be flooded past it.
            self._loading = None
            return
        load.buf.extend(chunk)
        if more:
            return  # more chunks to come, stay in the loading state
        self._loading = None
        if len(load.buf) != load.expected_size:
            return  # short transmission, discard rather than storing a bad image
        self.images[load.image_id] = Image(
            id=load.image_id, width=load.width, height=load.height, format=load.format, data=bytes(load.buf)
        )
        if load.display:
            self.placements.append(Placement(image_id=load.image_id, row=load.cursor_row, col=load.cursor_col))

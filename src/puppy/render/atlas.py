"""Glyph atlas: packs rasterized glyphs into a grid of fixed cell-sized slots
in a single image, tracked by glyph id. Pure CPU-side logic (numpy), no GPU
dependency -- GPU texture upload is a separate concern (gpu.py).

Design matches kitty's real sprite atlas (confirmed via cell.slang's
to_sprite_pos/sprites_xnum/sprites_ynum): every atlas slot is a full
cell_width x cell_height box, and each glyph is baseline-positioned *within*
its slot at rasterization time (bearing_x horizontally, ascender - bearing_y
vertically) -- not stored at its own tighter bitmap size with a variable UV
rect. This means every cell's draw quad samples a plain, uniform
cell-sized region of the atlas; the renderer doesn't need per-glyph size/
offset data at draw time, just which slot. Simpler than variable-sized UV
rects, and it's what the reference implementation actually does.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .font import GlyphBitmap


@dataclass
class AtlasSlot:
    col: int
    row: int
    u0: float
    v0: float
    u1: float
    v1: float


class GlyphAtlas:
    def __init__(self, cell_width: int, cell_height: int, ascender: int, cols: int = 32, rows: int = 32) -> None:
        self.cell_width = cell_width
        self.cell_height = cell_height
        self.ascender = ascender
        self.cols = cols
        self.rows = rows
        self.width = cell_width * cols
        self.height = cell_height * rows
        self.image = np.zeros((self.height, self.width), dtype=np.uint8)
        self._slots: dict[int, AtlasSlot] = {}
        self._next_index = 0
        # (x, y, w, h) of the region needing GPU re-upload since the last
        # clear_dirty() call, or None if nothing changed.
        self.dirty_rect: tuple[int, int, int, int] | None = None

    @property
    def is_full(self) -> bool:
        return self._next_index >= self.cols * self.rows

    def get_or_add(self, glyph_id: int, bitmap: GlyphBitmap) -> AtlasSlot:
        existing = self._slots.get(glyph_id)
        if existing is not None:
            return existing
        if self.is_full:
            raise RuntimeError(f"glyph atlas full: {self.cols}x{self.rows} slots exhausted")
        col = self._next_index % self.cols
        row = self._next_index // self.cols
        self._next_index += 1

        cell_x = col * self.cell_width
        cell_y = row * self.cell_height
        self._blit_baseline_positioned(cell_x, cell_y, bitmap)

        slot = AtlasSlot(
            col=col,
            row=row,
            u0=cell_x / self.width,
            v0=cell_y / self.height,
            u1=(cell_x + self.cell_width) / self.width,
            v1=(cell_y + self.cell_height) / self.height,
        )
        self._slots[glyph_id] = slot
        return slot

    def _blit_baseline_positioned(self, cell_x: int, cell_y: int, bitmap: GlyphBitmap) -> None:
        if bitmap.width <= 0 or bitmap.height <= 0:
            return  # space and other zero-size glyphs: leave the slot blank
        # bearing_x: offset from cell-left to bitmap-left. ascender - bearing_y:
        # offset from cell-top to bitmap-top (bitmap_top is bearing above the
        # baseline; the baseline itself sits `ascender` px below the cell top).
        off_x = bitmap.bearing_x
        off_y = self.ascender - bitmap.bearing_y

        pixels = np.frombuffer(bitmap.pixels, dtype=np.uint8).reshape(bitmap.height, bitmap.width)

        # Clip against the cell bounds in all four directions -- a Nerd Font
        # glyph or a negative/overflowing bearing must never write outside
        # its slot and corrupt a neighboring glyph.
        src_x0 = max(0, -off_x)
        src_y0 = max(0, -off_y)
        dst_x0 = max(0, off_x)
        dst_y0 = max(0, off_y)
        copy_w = min(bitmap.width - src_x0, self.cell_width - dst_x0)
        copy_h = min(bitmap.height - src_y0, self.cell_height - dst_y0)
        if copy_w <= 0 or copy_h <= 0:
            return

        dst_x = cell_x + dst_x0
        dst_y = cell_y + dst_y0
        self.image[dst_y:dst_y + copy_h, dst_x:dst_x + copy_w] = (
            pixels[src_y0:src_y0 + copy_h, src_x0:src_x0 + copy_w]
        )
        self._mark_dirty(dst_x, dst_y, copy_w, copy_h)

    def _mark_dirty(self, x: int, y: int, w: int, h: int) -> None:
        if self.dirty_rect is None:
            self.dirty_rect = (x, y, w, h)
            return
        dx, dy, dw, dh = self.dirty_rect
        nx, ny = min(dx, x), min(dy, y)
        nx2, ny2 = max(dx + dw, x + w), max(dy + dh, y + h)
        self.dirty_rect = (nx, ny, nx2 - nx, ny2 - ny)

    def clear_dirty(self) -> None:
        self.dirty_rect = None

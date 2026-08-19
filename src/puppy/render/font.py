"""HarfBuzz shaping + FreeType rasterization for a fixed-cell terminal grid.

Cell width/height come from FreeType's HINTED metrics for a reference glyph
(space), not HarfBuzz's or FreeType's unhinted/design-unit values. Confirmed
empirically: hinting snaps a genuinely monospace font's advance to a clean
integer pixel grid (10.0px hinted vs 9.640625px unhinted at 16px, DejaVu
Markup Nerd Font, verified equal across 'M'/'i'/'W'/'.') -- a terminal needs
that integer grid so cells align without fractional-pixel drift accumulating
across a row. HarfBuzz's own x_advance is intentionally NOT used for cell
positioning: every cell sits at its fixed column*cell_width regardless of a
glyph's natural advance, matching how a monospace grid actually works (this is
also how kitty/alacritty/etc. treat it).

Known, documented scope limit: shaping is done per-character (matching
Screen.Cell's one-character-per-cell model), not per-line/run. Complex-script
contextual joining (Arabic, etc.) and true multi-character ligatures need
shaping a contiguous run of cells together -- not built yet, a real gap, not
an oversight.

Bold uses FreeType's real synthetic emboldening (FT_GlyphSlot_Embolden, an
outline-based thickening applied before rasterization -- confirmed to
actually add ink, not a no-op: ~41% more covered pixels on a test glyph),
not a dedicated bold font file. This is the standard real fallback technique
terminal emulators use when a proper bold face isn't loaded; a real bold
font variant would look better but isn't wired up here, a documented gap.
"""
from __future__ import annotations

from dataclasses import dataclass

import freetype
import uharfbuzz as hb


@dataclass
class GlyphBitmap:
    width: int
    height: int
    bearing_x: int  # left bearing, px, offset from cell-left to bitmap-left
    bearing_y: int  # top bearing, px, offset from baseline to bitmap-top
    pixels: bytes  # width*height, one byte per pixel, 8-bit grayscale alpha


class FontRenderer:
    def __init__(self, font_path: str, pixel_size: int) -> None:
        self.font_path = font_path
        self.pixel_size = pixel_size

        self._ft_face = freetype.Face(font_path)
        self._ft_face.set_pixel_sizes(0, pixel_size)

        blob = hb.Blob.from_file_path(font_path)
        hb_face = hb.Face(blob)
        self._hb_font = hb.Font(hb_face)

        self._ft_face.load_char(" ", freetype.FT_LOAD_RENDER)
        self.cell_width = round(self._ft_face.glyph.advance.x / 64)
        metrics = self._ft_face.size
        self.ascender = round(metrics.ascender / 64)
        descender = round(metrics.descender / 64)  # FreeType reports this negative
        self.cell_height = self.ascender - descender

        # face.underline_position/thickness are in *font design units*, unlike
        # face.size.ascender/descender used above (confirmed by reading
        # freetype-py's actual property source: they read straight from the
        # unscaled FT_Face struct, not the size-scaled one) -- must be scaled
        # by pixel_size/units_per_EM manually, same convention HarfBuzz's
        # design-unit-space advances use.
        upem = self._ft_face.units_per_EM
        underline_position = round(self._ft_face.underline_position * pixel_size / upem)
        self.underline_thickness = max(1, round(self._ft_face.underline_thickness * pixel_size / upem))
        # underline_position is the offset of the underline's center from the
        # baseline (negative = below, per FreeType convention); convert to a
        # top-down pixel offset within the cell (0 = cell top).
        self.underline_y = self.ascender - underline_position

        self._glyph_cache: dict[tuple[int, bool], GlyphBitmap] = {}
        self._char_to_glyph_id: dict[str, int] = {}

    def glyph_id_for_char(self, ch: str) -> int:
        """Shapes a single character and returns its glyph id, cached by
        character -- a real-time renderer calling this every frame for every
        visible cell must not re-run HarfBuzz shaping each time. If shaping
        produces more than one glyph (a real possibility for some combining-
        mark/complex-script input even for one input character), only the
        first is used -- multi-glyph-per-character shaping isn't handled yet,
        see the module docstring's scope limit.
        """
        cached = self._char_to_glyph_id.get(ch)
        if cached is not None:
            return cached
        buf = hb.Buffer()
        buf.add_str(ch)
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf)
        infos = buf.glyph_infos
        glyph_id = infos[0].codepoint if infos else 0  # 0 = .notdef
        self._char_to_glyph_id[ch] = glyph_id
        return glyph_id

    def rasterize(self, glyph_id: int, bold: bool = False) -> GlyphBitmap:
        cache_key = (glyph_id, bold)
        cached = self._glyph_cache.get(cache_key)
        if cached is not None:
            return cached
        # FT_LOAD_RENDER == FT_LOAD_DEFAULT followed by an explicit render()
        # call (confirmed byte-for-byte identical output) -- split into two
        # steps so FT_GlyphSlot_Embolden can run in between for bold.
        self._ft_face.load_glyph(glyph_id, freetype.FT_LOAD_DEFAULT)
        if bold:
            freetype.FT_GlyphSlot_Embolden(self._ft_face.glyph._FT_GlyphSlot)
        self._ft_face.glyph.render(freetype.FT_RENDER_MODE_NORMAL)
        slot = self._ft_face.glyph
        bitmap = slot.bitmap
        # FreeType may pad each row to an alignment boundary (pitch >= width);
        # bitmap.buffer is pitch*rows bytes, not width*rows -- must slice per
        # row and discard the padding, never assume pitch == width even though
        # it happens to match for some glyphs/sizes.
        raw = bitmap.buffer
        pixels = bytearray(bitmap.width * bitmap.rows)
        for row in range(bitmap.rows):
            src_start = row * bitmap.pitch
            dst_start = row * bitmap.width
            pixels[dst_start:dst_start + bitmap.width] = raw[src_start:src_start + bitmap.width]
        glyph = GlyphBitmap(
            width=bitmap.width,
            height=bitmap.rows,
            bearing_x=slot.bitmap_left,
            bearing_y=slot.bitmap_top,
            pixels=bytes(pixels),
        )
        self._glyph_cache[cache_key] = glyph
        return glyph

    def rasterize_char(self, ch: str, bold: bool = False) -> GlyphBitmap:
        return self.rasterize(self.glyph_id_for_char(ch), bold=bold)

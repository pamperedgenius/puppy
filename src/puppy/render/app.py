"""Wires PtySession -> Parser -> Screen -> FontRenderer/GlyphAtlas ->
CellRenderer -> a live Window into an actual running program. This is what
`python -m puppy.render.app` runs -- the pass-through text harness in
__main__.py remains the separate, already-tested, headless-friendly way to
validate the PTY/parser/screen layers without a GPU/display.

v1 scope: full-grid redraw every frame (no dirty-cell tracking), no key/mouse
input wired yet (see PROGRESS.md's next steps), colors resolved via
puppy.render.palette (ansi256 + Screen.palette OSC-4 overrides), reverse video
handled by swapping fg/bg, bold/underline attributes not yet visually
rendered (no bold font variant or underline decoration sprite built yet --
deliberate, documented gap, matching the same scoping as the rest of this
render pass).
"""
from __future__ import annotations

import select
import shutil
import subprocess

import numpy as np

from ..parser import Parser
from ..pty_session import PtySession
from ..screen import Screen
from .atlas import GlyphAtlas
from .cell_renderer import INSTANCE_DTYPE, CellRenderer
from .color import srgb_color
from .font import FontRenderer
from .palette import resolve_color
from .window import Window

DEFAULT_FG = (255, 255, 255)
DEFAULT_BG = (0, 0, 0)


def find_monospace_font() -> str:
    if not shutil.which("fc-match"):
        raise RuntimeError("fc-match not found -- can't resolve a system font (fontconfig missing?)")
    result = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True)
    path = result.stdout.strip()
    if not path:
        raise RuntimeError("fc-match found no monospace font")
    return path


def build_instances(screen: Screen, font: FontRenderer, atlas: GlyphAtlas) -> np.ndarray:
    instances = np.zeros(screen.rows * screen.cols, dtype=INSTANCE_DTYPE)
    idx = 0
    for row_idx, row in enumerate(screen.grid):
        for col_idx, cell in enumerate(row):
            glyph_id = font.glyph_id_for_char(cell.char)
            bitmap = font.rasterize(glyph_id)
            slot = atlas.get_or_add(glyph_id, bitmap)

            fg_rgb = resolve_color(cell.fg, DEFAULT_FG, screen.palette)
            bg_rgb = resolve_color(cell.bg, DEFAULT_BG, screen.palette)
            if cell.reverse:
                fg_rgb, bg_rgb = bg_rgb, fg_rgb

            instances[idx] = (col_idx, row_idx, slot.col, slot.row, srgb_color(*fg_rgb), srgb_color(*bg_rgb))
            idx += 1
    return instances


def run(rows: int = 24, cols: int = 80, pixel_size: int = 16) -> None:
    font_path = find_monospace_font()
    font = FontRenderer(font_path, pixel_size)
    atlas = GlyphAtlas(font.cell_width, font.cell_height, font.ascender)
    window = Window(rows, cols, font.cell_width, font.cell_height, title="puppy")
    renderer = CellRenderer(window.gpu, atlas, rows, cols)

    screen = Screen(rows, cols)
    parser = Parser(screen)
    session = PtySession(rows=rows, cols=cols)

    def draw_frame() -> None:
        renderer.render(build_instances(screen, font, atlas))

    try:
        while not window.should_close():
            window.poll_events()
            readable, _, _ = select.select([session.master_fd], [], [], 0.01)
            if session.master_fd in readable:
                try:
                    data = session.read()
                except OSError:
                    break
                if not data:
                    break
                parser.feed(data)
            window.canvas.request_draw(draw_frame)
            window.canvas.force_draw()
    finally:
        window.close()
        session.close()


if __name__ == "__main__":
    run()

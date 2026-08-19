"""Wires PtySession -> Parser -> Screen -> FontRenderer/GlyphAtlas ->
CellRenderer -> a live Window into an actual running program. This is what
`python -m puppy.render.app` runs -- the pass-through text harness in
__main__.py remains the separate, already-tested, headless-friendly way to
validate the PTY/parser/screen layers without a GPU/display.

v1 scope: full-grid redraw every frame (no dirty-cell tracking), colors
resolved via puppy.render.palette (ansi256 + Screen.palette OSC-4 overrides),
reverse video handled by swapping fg/bg. Bold uses FreeType's real synthetic
emboldening (see font.py), underline is drawn at the font's real
underline_y/thickness metrics (see cell_renderer.py) -- neither is a
placeholder. Key/mouse input is wired via puppy.render.input_state's
InputState, including the kitty keyboard protocol once a program opts in
(see puppy.kitty_keyboard's module docstring for its scope limits).
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
from .graphics_renderer import GraphicsRenderer
from .input_state import InputState
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
            bitmap = font.rasterize(glyph_id, bold=cell.bold)
            slot = atlas.get_or_add((glyph_id, cell.bold), bitmap)

            fg_rgb = resolve_color(cell.fg, DEFAULT_FG, screen.palette)
            bg_rgb = resolve_color(cell.bg, DEFAULT_BG, screen.palette)
            if cell.reverse:
                fg_rgb, bg_rgb = bg_rgb, fg_rgb

            flags = (1.0 if cell.underline else 0.0, 0.0, 0.0, 0.0)
            instances[idx] = (col_idx, row_idx, slot.col, slot.row, srgb_color(*fg_rgb), srgb_color(*bg_rgb), flags)
            idx += 1
    return instances


def run(rows: int = 24, cols: int = 80, pixel_size: int = 16) -> None:
    font_path = find_monospace_font()
    font = FontRenderer(font_path, pixel_size)
    atlas = GlyphAtlas(font.cell_width, font.cell_height, font.ascender)
    window = Window(rows, cols, font.cell_width, font.cell_height, title="puppy")
    renderer = CellRenderer(
        window.gpu, atlas, rows, cols, underline_y=font.underline_y, underline_thickness=font.underline_thickness
    )
    graphics_renderer = GraphicsRenderer(window.gpu)

    session = PtySession(rows=rows, cols=cols)
    screen = Screen(rows, cols, write_back=session.write)
    parser = Parser(screen)

    input_state = InputState(session, screen, font)
    window.set_key_handler(input_state.on_key)
    window.set_char_handler(input_state.on_char)
    window.set_mouse_button_handler(input_state.on_mouse_button)
    window.set_cursor_pos_handler(input_state.on_cursor_pos)
    window.set_scroll_handler(input_state.on_scroll)

    def draw_frame() -> None:
        renderer.render(build_instances(screen, font, atlas))
        graphics_renderer.render(screen.graphics, cols=cols, rows=rows, cell_width=font.cell_width, cell_height=font.cell_height)

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

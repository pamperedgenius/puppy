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

Default colors come from RengeOS's active theme-switcher scheme (see
theme.py) -- ANSI 0-15 are pre-loaded into Screen.palette exactly as if a
program had set them via OSC 4, so the same resolve_color() path already
used for real OSC-4 overrides handles it, no separate color path. Real
terminal resize is handled (see the on_resize handler below and
CellRenderer.resize): the window's framebuffer size, not its requested
initial size, drives Screen/PtySession/CellRenderer's actual grid dimensions
-- niri (like most tiling compositors) overrides an app's requested initial
window size from its own layout policy, so this isn't an edge case, it's the
normal case on this system. See PROGRESS.md's 2026-08-20 entry for the full
diagnosis (this was the root cause of a real "font renders too
big"/"rendering looks wrong" bug report) and for the separate window.py fix
that makes the window actually respond to a WM close request (e.g. niri's
Mod+Q) -- app.py's own loop was never the problem there, but is why that fix
lives in Window.should_close() rather than here.
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
from .theme import DEFAULT_BG, DEFAULT_FG, load_theme
from .window import Window


def find_monospace_font() -> str:
    if not shutil.which("fc-match"):
        raise RuntimeError("fc-match not found -- can't resolve a system font (fontconfig missing?)")
    result = subprocess.run(["fc-match", "-f", "%{file}", "monospace"], capture_output=True, text=True)
    path = result.stdout.strip()
    if not path:
        raise RuntimeError("fc-match found no monospace font")
    return path


def find_bold_monospace_font() -> str | None:
    """Best-effort: a distinct real bold face, matching how xfce4-terminal/
    Pango render bold rather than FreeType's synthetic embolden alone (see
    font.py's module docstring for why this matters -- a real, measured pixel-
    density difference, not just cosmetic preference). Returns None (falls
    back to synthetic embolden) if fc-match is missing or fails; FontRenderer
    itself also falls back if the resolved path is identical to the regular
    face (fontconfig's own signal that no true bold weight exists)."""
    if not shutil.which("fc-match"):
        return None
    result = subprocess.run(["fc-match", "-f", "%{file}", "monospace:bold"], capture_output=True, text=True)
    path = result.stdout.strip()
    return path or None


def build_instances(
    screen: Screen, font: FontRenderer, atlas: GlyphAtlas, default_fg: tuple[int, int, int] = DEFAULT_FG, default_bg: tuple[int, int, int] = DEFAULT_BG
) -> np.ndarray:
    instances = np.zeros(screen.rows * screen.cols, dtype=INSTANCE_DTYPE)
    idx = 0
    for row_idx, row in enumerate(screen.grid):
        for col_idx, cell in enumerate(row):
            glyph_id = font.glyph_id_for_char(cell.char, bold=cell.bold)
            bitmap = font.rasterize(glyph_id, bold=cell.bold)
            slot = atlas.get_or_add((glyph_id, cell.bold), bitmap)

            fg_rgb = resolve_color(cell.fg, default_fg, screen.palette)
            bg_rgb = resolve_color(cell.bg, default_bg, screen.palette)
            if cell.reverse:
                fg_rgb, bg_rgb = bg_rgb, fg_rgb

            flags = (1.0 if cell.underline else 0.0, 0.0, 0.0, 0.0)
            instances[idx] = (col_idx, row_idx, slot.col, slot.row, srgb_color(*fg_rgb), srgb_color(*bg_rgb), flags)
            idx += 1
    return instances


def run(rows: int = 24, cols: int = 80, pixel_size: int = 16) -> None:
    theme = load_theme()

    font_path = find_monospace_font()
    bold_font_path = find_bold_monospace_font()
    font = FontRenderer(font_path, pixel_size, bold_font_path=bold_font_path)
    atlas = GlyphAtlas(font.cell_width, font.cell_height, font.ascender)
    window = Window(rows, cols, font.cell_width, font.cell_height, title="puppy")

    # Real, confirmed bug fixed here (2026-08-20, found live-testing the
    # previous resize fix): niri assigns the window its real tiled size
    # during creation itself (confirmed: window.get_framebuffer_size()
    # already returns it right after Window() returns, no settling needed),
    # but this code used to keep spawning the PTY/Screen at the *requested*
    # rows/cols (24x80) regardless, relying on the async framebuffer-resize
    # callback to correct it later. The shell's rc file (which auto-runs a
    # fetch tool here) starts printing immediately at the wrong 80-column
    # width, and when the resize callback then calls screen.resize() mid
    # print, the grid rewraps at a *different* column boundary than the
    # already-in-flight output assumed -- corrupting already-correct lines
    # (confirmed by reproducing it directly against Screen/PtySession,
    # bypassing the GPU entirely, with a resize injected mid-stream). Fix:
    # query the real size up front and use it for the PTY's initial size, so
    # the shell never starts at the wrong width in the first place.
    real_width, real_height = window.get_framebuffer_size()
    cols = max(1, real_width // font.cell_width)
    rows = max(1, real_height // font.cell_height)

    renderer = CellRenderer(
        window.gpu, atlas, rows, cols, underline_y=font.underline_y, underline_thickness=font.underline_thickness
    )
    graphics_renderer = GraphicsRenderer(window.gpu)

    session = PtySession(rows=rows, cols=cols)
    screen = Screen(rows, cols, write_back=session.write)
    for index, spec in theme.ansi.items():
        screen.set_palette_color(index, spec)
    parser = Parser(screen)

    input_state = InputState(session, screen, font)
    window.set_key_handler(input_state.on_key)
    window.set_char_handler(input_state.on_char)
    window.set_mouse_button_handler(input_state.on_mouse_button)
    window.set_cursor_pos_handler(input_state.on_cursor_pos)
    window.set_scroll_handler(input_state.on_scroll)

    # Real, confirmed bug fixed here (2026-08-20): the window was never resized
    # after opening, but niri (like most tiling compositors) does not honor an
    # app's requested initial size -- it sizes new windows from its own
    # default-column-width policy instead (confirmed: `default-column-width
    # { proportion 0.5; }` in this system's niri config). That meant the fixed
    # rows*cell_width x cols*cell_height grid CellRenderer was built for got
    # stretched to fill whatever (larger, in practice) surface niri actually
    # gave the window -- the real cause of both "font too big" and "rendering
    # not properly" (upscaled/misaligned glyphs), not two separate bugs.
    def on_resize(width: int, height: int) -> None:
        new_cols = max(1, width // font.cell_width)
        new_rows = max(1, height // font.cell_height)
        if new_cols == screen.cols and new_rows == screen.rows:
            return
        screen.resize(new_rows, new_cols)
        session.resize(new_rows, new_cols)
        renderer.resize(new_rows, new_cols)

    window.set_framebuffer_size_handler(on_resize)

    def draw_frame() -> None:
        renderer.render(build_instances(screen, font, atlas, default_fg=theme.fg, default_bg=theme.bg))
        graphics_renderer.render(screen.graphics, cols=screen.cols, rows=screen.rows, cell_width=font.cell_width, cell_height=font.cell_height)

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

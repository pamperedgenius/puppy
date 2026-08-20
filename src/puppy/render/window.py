"""A live GLFW window wrapping a GpuContext -- the interactive counterpart to
the offscreen canvas the tests use. No pytest coverage here (needs a real
display); see PROGRESS.md for the live-test status.

Input handlers register *raw* GLFW callbacks directly on the underlying GLFW
window handle (`self.canvas._window`, a private rendercanvas attribute --
deliberate, verified, not an oversight: rendercanvas.glfw's own key handler
silently *drops every GLFW_REPEAT event* (confirmed by reading its source,
`_on_key`'s `else: # glfw.REPEAT / return`), which is exactly the granularity
puppy chose GLFW for -- the kitty keyboard protocol needs real press/repeat/
release. Using rendercanvas's higher-level event abstraction would silently
defeat that. Confirmed safe to override: rendercanvas registers *separate*
callbacks for resize/close/focus/iconify that this doesn't touch, only the
6 input callbacks (key/char/mouse-button/cursor-pos/scroll/cursor-enter) are
replaced, and those are only used internally for rendercanvas's own discarded
input abstraction, not anything render-loop-critical.
"""
from __future__ import annotations

import glfw
from rendercanvas.glfw import RenderCanvas

from .gpu import GpuContext


class Window:
    def __init__(self, rows: int, cols: int, cell_width: int, cell_height: int, title: str = "puppy") -> None:
        width = max(1, cols * cell_width)
        height = max(1, rows * cell_height)
        self.canvas = RenderCanvas(size=(width, height), title=title)
        self.gpu = GpuContext.create(self.canvas)

    @property
    def _glfw_handle(self):
        return self.canvas._window

    def get_framebuffer_size(self) -> tuple[int, int]:
        """Real current framebuffer pixel size. Confirmed empirically (not
        assumed): GLFW's Wayland backend already does the compositor
        round-trip during window creation, so this reflects niri's actual
        assigned size (e.g. its `default-column-width` tiling policy)
        immediately after `Window.__init__` returns -- no poll_events()
        settling loop needed. See app.py's `run()` for why the caller must
        use this instead of its originally-requested rows/cols before
        spawning the PTY."""
        return glfw.get_framebuffer_size(self._glfw_handle)

    def should_close(self) -> bool:
        # Real, confirmed bug fixed here (2026-08-20): app.py's main loop calls
        # glfw.poll_events() directly (raw GLFW, bypassing rendercanvas's own
        # event loop) so it never reaches rendercanvas's _rc_gui_poll -> _maybe_close,
        # the only place that was checking glfw.window_should_close() and turning it
        # into canvas.get_closed()==True. That meant a WM close request (Mod+Q in
        # niri, which asks GLFW's Wayland backend to set the close flag exactly like
        # any other platform's window-manager close button) was silently never
        # detected -- not a niri/Wayland compatibility problem, puppy just never
        # looked. Checking the raw GLFW flag directly here is a self-contained fix
        # that doesn't depend on rendercanvas's private _rc_* polling internals.
        if self.canvas.get_closed():
            return True
        return glfw.window_should_close(self._glfw_handle)

    def poll_events(self) -> None:
        glfw.poll_events()

    def close(self) -> None:
        if not self.canvas.get_closed():
            self.canvas.close()

    # --- raw input callbacks (see module docstring for why these bypass
    # rendercanvas's own event abstraction) ---

    def set_key_handler(self, handler) -> None:
        """handler(key: int, scancode: int, action: int, mods: int) -- action
        is glfw.PRESS/RELEASE/REPEAT, all three delivered (unlike
        rendercanvas's own handler)."""
        glfw.set_key_callback(self._glfw_handle, lambda _win, k, s, a, m: handler(k, s, a, m))

    def set_char_handler(self, handler) -> None:
        """handler(codepoint: int) -- only fires for actual text-producing
        key presses, already respecting layout/shift/IME."""
        glfw.set_char_callback(self._glfw_handle, lambda _win, cp: handler(cp))

    def set_mouse_button_handler(self, handler) -> None:
        """handler(button: int, action: int, mods: int)."""
        glfw.set_mouse_button_callback(self._glfw_handle, lambda _win, b, a, m: handler(b, a, m))

    def set_cursor_pos_handler(self, handler) -> None:
        """handler(xpos: float, ypos: float) -- window-relative pixel coords."""
        glfw.set_cursor_pos_callback(self._glfw_handle, lambda _win, x, y: handler(x, y))

    def set_scroll_handler(self, handler) -> None:
        """handler(xoffset: float, yoffset: float)."""
        glfw.set_scroll_callback(self._glfw_handle, lambda _win, x, y: handler(x, y))

    def set_framebuffer_size_handler(self, handler) -> None:
        """handler(width: int, height: int) -- real framebuffer pixel size,
        fires on every resize. rendercanvas already registers its own
        framebuffer-size callback (to reconfigure the wgpu surface, which
        this doesn't touch/replace -- see the module docstring on why input
        callbacks specifically are overridden but resize/close/focus aren't);
        GLFW supports multiple callbacks are NOT stacked, only the most
        recently registered one fires, so this intentionally chains to
        rendercanvas's own handler first before calling the caller's."""
        rc_handler = glfw.set_framebuffer_size_callback(self._glfw_handle, None)  # peek/clear, restored below

        def _on_resize(_win, width, height):
            if rc_handler is not None:
                rc_handler(_win, width, height)
            handler(width, height)

        glfw.set_framebuffer_size_callback(self._glfw_handle, _on_resize)

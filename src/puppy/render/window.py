"""A live GLFW window wrapping a GpuContext -- the interactive counterpart to
the offscreen canvas the tests use. No pytest coverage here (needs a real
display); see PROGRESS.md for the live-test status. Key/mouse-event callback
wiring (toward puppy.mouse's SGR encoder, and eventually the kitty keyboard
protocol) is the next slice, not built yet -- this is window lifecycle only.
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

    def should_close(self) -> bool:
        return self.canvas.get_closed()

    def poll_events(self) -> None:
        glfw.poll_events()

    def close(self) -> None:
        if not self.canvas.get_closed():
            self.canvas.close()

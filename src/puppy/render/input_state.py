"""Tracks live input state (held modifiers, held mouse button, last cell
position) and turns raw GLFW callback data into bytes written to a PTY.

Split out from app.py since it's a distinct concern (input -> bytes) from the
render loop (Screen -> pixels) -- same "sink" dependency-injection pattern
used throughout this project (session only needs a `.write(bytes)` method,
so tests can substitute a stub instead of a real PtySession).

GLFW's key/mouse-button callbacks receive an accurate `mods` bitmask on every
call; cursor-move and scroll callbacks do not, so the most recently seen mods
value is cached and reused for those.

Kitty keyboard protocol note: once `Screen.key_encoding_flags` is nonzero
(the running program opted in via `CSI = flags ; mode u`), the GLFW *char*
callback is deliberately suppressed here -- otherwise a mapped key (a letter,
say) would be reported twice: once via the key callback's CSI-u sequence and
again via the char callback's plain UTF-8 text, which real kitty-protocol-
aware programs don't expect. Real, documented consequence: keys
`puppy.kitty_keyboard.encode_kitty_key_event` doesn't map yet (most
punctuation -- see its module docstring) produce *no* input at all while
enhanced mode is active, not even plain text -- a real v1 gap, not silently
papered over.

Text selection note: a left-button press/drag/release is routed to one of two
completely separate places, decided once at press time -- `Screen.
mouse_reporting_active` (a real xterm/kitty convention: a running program that
has claimed mouse tracking gets clicks normally, *unless* Shift is held, which
always forces local selection regardless). Local selection never also writes
a mouse report for the same press/release, matching how a real terminal
doesn't double-deliver a click it's handling itself. See screen.py's
selection methods for the model half; copy_to_clipboard (constructor-
injected, no-op default -- same pattern as Screen.write_back) is called once,
on release, with the finished selection's text.

Scrollback-view note: wheel scroll follows the exact same
"local-vs-forwarded" split as selection -- `screen.mouse_reporting_active`
gates whether a wheel event moves puppy's own viewport into history
(`Screen.scroll_view`) or gets reported to the program as a real SCROLL_UP/
SCROLL_DOWN button event, and it's additionally suppressed on the alt screen
(`screen.in_alt_screen`) since vim/less/htop already scroll themselves --
forwarding *and* locally scrolling would be double behavior. Unlike
selection there's no Shift override here: real terminals don't have one for
wheel scroll (Shift-scroll on kitty/xterm answers to a *different* real
convention -- fast/inverse scroll -- puppy doesn't implement, not simulated
here).
"""
from __future__ import annotations

from typing import Callable

import glfw

from ..keyboard import encode_char, encode_key
from ..kitty_keyboard import encode_kitty_key_event
from ..mouse import MouseAction, MouseButton, generate_mouse_report

_GLFW_BUTTON_MAP = {
    glfw.MOUSE_BUTTON_LEFT: MouseButton.LEFT,
    glfw.MOUSE_BUTTON_RIGHT: MouseButton.RIGHT,
    glfw.MOUSE_BUTTON_MIDDLE: MouseButton.MIDDLE,
}

_DECCKM_MODE = 1
_WHEEL_SCROLL_MULTIPLIER = 5  # kitty's real wheel_scroll_multiplier default


class InputState:
    def __init__(self, session, screen, font, copy_to_clipboard: Callable[[str], None] | None = None) -> None:
        self.session = session
        self.screen = screen
        self.font = font
        # No-op default so every existing test/use that doesn't care about
        # selection keeps working unchanged -- same pattern as Screen.
        # write_back.
        self.copy_to_clipboard: Callable[[str], None] = copy_to_clipboard or (lambda text: None)
        self.current_mods = 0
        self.pressed_button: MouseButton | None = None
        self.last_col = 0
        self.last_row = 0
        # True while a local (non-reported) left-button drag is selecting
        # text, distinct from pressed_button (which also tracks button-held
        # state for the mouse-*reporting* path).
        self.selecting = False

    def on_key(self, key: int, scancode: int, action: int, mods: int) -> None:
        self.current_mods = mods
        if action == glfw.PRESS:
            # Typing deselects and snaps back to the live bottom, matching
            # every real terminal -- a stale selection sitting under text a
            # program just overwrote, or typing into a program while looking
            # at history, would both be actively misleading, not just
            # cosmetic. The keystroke itself still gets sent normally below,
            # same as a real terminal exiting scrollback on any keypress.
            self.screen.clear_selection()
            self.screen.reset_scroll_view()
        if self.screen.key_encoding_flags:
            data = encode_kitty_key_event(key, action, mods, self.screen.key_encoding_flags)
            if data:
                self.session.write(data)
            return
        if action == glfw.RELEASE:
            return  # legacy encoding only sends bytes on press/repeat, matching real terminals
        decckm = _DECCKM_MODE in self.screen.private_modes
        data = encode_key(key, mods, decckm=decckm)
        if data:
            self.session.write(data)

    def on_char(self, codepoint: int) -> None:
        if self.screen.key_encoding_flags:
            return  # suppressed once kitty protocol is active, see module docstring
        self.session.write(encode_char(codepoint))

    def on_mouse_button(self, button: int, action: int, mods: int) -> None:
        self.current_mods = mods
        mb = _GLFW_BUTTON_MAP.get(button)
        if mb is None:
            return  # extra/side buttons not mapped yet, documented gap
        if mb is MouseButton.LEFT and action == glfw.PRESS:
            shift = bool(mods & glfw.MOD_SHIFT)
            # Selection coordinates are always interpreted against the live
            # grid (Screen.cell_selected/selected_text never consult
            # visible_rows()) -- starting one while scrolled_back would
            # silently select/copy whatever's really at those row/col
            # positions in the live grid, not the scrollback content the
            # user can actually see (which is also never rendered as
            # selected either, see build_instances' viewing_live gate) --
            # so it's blocked here rather than shipped as an invisible,
            # copy-the-wrong-thing footgun.
            if (shift or not self.screen.mouse_reporting_active) and not self.screen.scrolled_back:
                self.selecting = True
                self.pressed_button = mb
                self.screen.start_selection(self.last_row, self.last_col)
                return
        if mb is MouseButton.LEFT and action == glfw.RELEASE and self.selecting:
            self.selecting = False
            self.pressed_button = None
            if self.screen.has_selection():
                self.copy_to_clipboard(self.screen.selected_text())
            return
        if action == glfw.PRESS:
            self.pressed_button = mb
            self._report(MouseAction.PRESS, mb)
        else:
            self._report(MouseAction.RELEASE, mb)
            self.pressed_button = None

    def on_cursor_pos(self, xpos: float, ypos: float) -> None:
        cols = max(1, self.screen.cols)
        rows = max(1, self.screen.rows)
        self.last_col = max(0, min(cols - 1, int(xpos // self.font.cell_width)))
        self.last_row = max(0, min(rows - 1, int(ypos // self.font.cell_height)))
        if self.selecting:
            self.screen.update_selection(self.last_row, self.last_col)
            return
        action = MouseAction.DRAG if self.pressed_button is not None else MouseAction.MOVE
        self._report(action, self.pressed_button)

    def on_scroll(self, xoffset: float, yoffset: float) -> None:
        if yoffset == 0:
            return
        if not self.screen.mouse_reporting_active and not self.screen.in_alt_screen:
            # kitty's real wheel_scroll_multiplier default is 5.0 lines per
            # low-precision wheel notch (confirmed against kitty's own
            # options/definition.py) -- ported as a flat multiplier here.
            # Real, documented gap: kitty additionally branches on
            # high-precision scrolling devices (trackpads, and wheel mice on
            # Wayland/macOS specifically), scrolling proportional to the raw
            # pixel delta instead of this fixed per-notch amount -- GLFW's
            # yoffset here doesn't expose that distinction, and puppy doesn't
            # attempt to infer it, so every device scrolls at this same flat
            # rate.
            lines = max(1, round(abs(yoffset) * _WHEEL_SCROLL_MULTIPLIER))
            self.screen.scroll_view(lines if yoffset > 0 else -lines)
            return
        button = MouseButton.SCROLL_UP if yoffset > 0 else MouseButton.SCROLL_DOWN
        self._report(MouseAction.PRESS, button)

    def _report(self, action: MouseAction, button: MouseButton | None) -> None:
        data = generate_mouse_report(
            self.screen,
            action,
            self.last_row,
            self.last_col,
            button,
            shift=bool(self.current_mods & glfw.MOD_SHIFT),
            alt=bool(self.current_mods & glfw.MOD_ALT),
            ctrl=bool(self.current_mods & glfw.MOD_CONTROL),
        )
        if data:
            self.session.write(data)

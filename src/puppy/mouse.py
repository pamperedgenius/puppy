"""SGR mouse-event encoding (CSI < Cb;Cx;Cy M/m) -- the reporting sequences a
terminal sends TO the child program on mouse interaction, the reverse direction
of Parser (which decodes what the child sends TO us).

Bit-encoding rules verified against kitty's real encode_mouse_event_impl/
encode_button (mouse.c), not derived from memory of the spec alone. Only the
modern SGR protocol (mode 1006) is implemented -- legacy X10/UTF8 encoding
(base-32-offset bytes, breaks past coordinate 223) is a deliberate, documented
gap, not an oversight; see PROGRESS.md.

No real mouse-event *source* exists yet (blocked on the windowing-toolkit
decision, see PROGRESS.md) -- this module is the pure "given an event, produce
the bytes" half, unit-tested against known encodings so it's ready to wire up
once there's something to drive it.
"""
from __future__ import annotations

from enum import Enum


class MouseButton(Enum):
    LEFT = 1
    MIDDLE = 2
    RIGHT = 3
    SCROLL_UP = 4
    SCROLL_DOWN = 5
    SCROLL_LEFT = 6
    SCROLL_RIGHT = 7


class MouseAction(Enum):
    PRESS = "press"
    RELEASE = "release"
    DRAG = "drag"  # motion while a button is held
    MOVE = "move"  # motion with no button held (only reported under mode 1003)


# Bit values match kitty's mouse.c exactly (SHIFT_INDICATOR/ALT_INDICATOR/
# CONTROL_INDICATOR/MOTION_INDICATOR/SCROLL_BUTTON_INDICATOR/EXTRA_BUTTON_INDICATOR).
_SHIFT = 1 << 2
_ALT = 1 << 3
_CTRL = 1 << 4
_MOTION = 1 << 5
_SCROLL = 1 << 6
_EXTRA = 1 << 7

# Private modes that gate whether/which mouse events get reported at all (xterm
# convention, layered independently of the 1006 SGR encoding format itself):
# 1000 = press/release only, 1002 = + drag, 1003 = + pure motion with no button.
_BUTTON_EVENT_MODE = 1000
_DRAG_EVENT_MODE = 1002
_ANY_EVENT_MODE = 1003
_SGR_MODE = 1006


def _encode_button(button: int | None) -> int | None:
    if button is None:
        return None
    if 8 <= button <= 11:
        return (button - 8) | _EXTRA
    if 4 <= button <= 7:
        return (button - 4) | _SCROLL
    if 1 <= button <= 3:
        return button - 1
    return None


def encode_sgr_mouse_event(
    action: MouseAction,
    row: int,
    col: int,
    button: MouseButton | int | None = None,
    shift: bool = False,
    alt: bool = False,
    ctrl: bool = False,
) -> bytes | None:
    """The CSI < Cb;Cx;Cy M/m bytes for one mouse event, or None if this event
    produces no report at all (e.g. PRESS/RELEASE/DRAG with no valid button).

    row/col are 0-indexed cell coordinates (matching Screen.cursor_row/col
    convention); the wire format is 1-indexed, converted here. This function
    does not consult mode-tracking state -- see generate_mouse_report for the
    mode-gated entry point that decides whether an event should be reported at
    all before encoding it.
    """
    btn = button.value if isinstance(button, MouseButton) else button
    if action is MouseAction.MOVE:
        cb = _encode_button(btn)
        if cb is None:
            cb = 3
        cb += _MOTION
    elif action is MouseAction.DRAG:
        cb = _encode_button(btn)
        if cb is None:
            return None
        cb |= _MOTION
    else:  # PRESS or RELEASE
        cb = _encode_button(btn)
        if cb is None:
            return None
    if shift:
        cb |= _SHIFT
    if alt:
        cb |= _ALT
    if ctrl:
        cb |= _CTRL
    final = "m" if action is MouseAction.RELEASE else "M"
    return f"\x1b[<{cb};{col + 1};{row + 1}{final}".encode("ascii")


def generate_mouse_report(
    screen,
    action: MouseAction,
    row: int,
    col: int,
    button: MouseButton | int | None = None,
    shift: bool = False,
    alt: bool = False,
    ctrl: bool = False,
) -> bytes | None:
    """Mode-gated entry point: consults screen.private_modes to decide whether
    this event should be reported at all, then encodes it via
    encode_sgr_mouse_event if so.
    """
    modes = screen.private_modes
    if _SGR_MODE not in modes:
        return None  # legacy X10/UTF8 encoding not implemented, see module docstring
    if action in (MouseAction.PRESS, MouseAction.RELEASE):
        if not (modes & {_BUTTON_EVENT_MODE, _DRAG_EVENT_MODE, _ANY_EVENT_MODE}):
            return None
    elif action is MouseAction.DRAG:
        if not (modes & {_DRAG_EVENT_MODE, _ANY_EVENT_MODE}):
            return None
    elif action is MouseAction.MOVE:
        if _ANY_EVENT_MODE not in modes:
            return None
    return encode_sgr_mouse_event(action, row, col, button, shift, alt, ctrl)

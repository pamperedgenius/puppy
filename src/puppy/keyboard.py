"""Legacy (non-kitty-protocol) key encoding: GLFW key/char events -> the byte
sequences a real shell/program expects on stdin. This is the baseline
xterm/VT220-style encoding puppy's own terminfo entry advertises (khome,
kcuu1 in application mode, etc. -- see terminfo/puppy.terminfo, built from
the same source, not re-derived independently).

v1 scope, documented gaps: no Alt/Meta-prefixed sequences, no Ctrl+non-letter
combos, no Shift+function-key variants (real capabilities in puppy's own
terminfo, just not wired here yet). The kitty keyboard protocol (CSI u) is a
separate, later milestone -- this module is purely the pre-existing-terminal-
standard fallback, matching how a real terminal behaves before an app opts
into progressive enhancement.
"""
from __future__ import annotations

import glfw

# DECCKM (mode 1) toggles cursor keys between normal/ANSI mode (\e[A) and
# application mode (\eOA) -- real VT100/ANSI standard behavior, not a puppy
# invention. Screen.private_modes already tracks mode 1 generically.
_ARROWS_NORMAL = {
    glfw.KEY_UP: b"\x1b[A",
    glfw.KEY_DOWN: b"\x1b[B",
    glfw.KEY_RIGHT: b"\x1b[C",
    glfw.KEY_LEFT: b"\x1b[D",
}
_ARROWS_APPLICATION = {
    glfw.KEY_UP: b"\x1bOA",
    glfw.KEY_DOWN: b"\x1bOB",
    glfw.KEY_RIGHT: b"\x1bOC",
    glfw.KEY_LEFT: b"\x1bOD",
}

# Fixed sequences regardless of DECCKM -- taken directly from puppy's own
# compiled terminfo entry's k* capabilities (khome/kend/kdch1/kich1/knp/kpp/
# kf1-12/kbs), kept consistent with what puppy already claims to support.
_FIXED_KEYS = {
    glfw.KEY_HOME: b"\x1bOH",
    glfw.KEY_END: b"\x1bOF",
    glfw.KEY_DELETE: b"\x1b[3~",
    glfw.KEY_INSERT: b"\x1b[2~",
    glfw.KEY_PAGE_DOWN: b"\x1b[6~",
    glfw.KEY_PAGE_UP: b"\x1b[5~",
    glfw.KEY_F1: b"\x1bOP",
    glfw.KEY_F2: b"\x1bOQ",
    glfw.KEY_F3: b"\x1bOR",
    glfw.KEY_F4: b"\x1bOS",
    glfw.KEY_F5: b"\x1b[15~",
    glfw.KEY_F6: b"\x1b[17~",
    glfw.KEY_F7: b"\x1b[18~",
    glfw.KEY_F8: b"\x1b[19~",
    glfw.KEY_F9: b"\x1b[20~",
    glfw.KEY_F10: b"\x1b[21~",
    glfw.KEY_F11: b"\x1b[23~",
    glfw.KEY_F12: b"\x1b[24~",
    glfw.KEY_ENTER: b"\r",
    glfw.KEY_BACKSPACE: b"\x7f",
    glfw.KEY_TAB: b"\t",
    glfw.KEY_ESCAPE: b"\x1b",
}


def encode_key(key: int, mods: int, decckm: bool = False) -> bytes | None:
    """Byte sequence for a non-printable/special key or a Ctrl+letter
    control byte, or None if this key produces nothing here -- either it's a
    plain printable char (use encode_char, driven by GLFW's separate char
    callback, not this one) or it's a documented v1 gap (see module
    docstring).
    """
    if key in _ARROWS_NORMAL:
        return _ARROWS_APPLICATION[key] if decckm else _ARROWS_NORMAL[key]
    if key in _FIXED_KEYS:
        return _FIXED_KEYS[key]
    if mods & glfw.MOD_CONTROL and glfw.KEY_A <= key <= glfw.KEY_Z:
        return bytes([key - glfw.KEY_A + 1])
    return None


def encode_char(codepoint: int) -> bytes:
    return chr(codepoint).encode("utf-8")

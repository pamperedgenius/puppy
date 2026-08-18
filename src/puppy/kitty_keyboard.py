"""The kitty keyboard protocol (CSI u progressive enhancement), layered on
top of puppy.keyboard's legacy encoding -- only used once a program has
opted in via `CSI = flags ; mode u` (see Screen.key_encoding_flags).

Format verified against kitty's real key_encoding.c serialize():
    CSI key[:shifted_key][:alternate_key] ; mods[:action] ; text... u
v1 only ever emits the `CSI key ; mods[:action] u` form -- no alternate-key
or embedded-text subfields (real, documented gaps, see below).

Modifier bit values (SHIFT=1, ALT=2, CTRL=4, SUPER=8, HYPER=16, META=32,
CAPS_LOCK=64, NUM_LOCK=128; wire value = 1 + sum of bits) and the action
subfield (press=1, repeat=2, release=3, i.e. KeyAction+1) confirmed exact
from key_encoding.c. Functional-key codepoints (Private Use Area, 0xE000+)
confirmed exact from glfw-wrapper.h's GLFW_FKEY_* enum, which kitty's own
protocol spec publishes as the wire codepoints directly (kitty deliberately
made its internal fork's key ids equal the wire codepoints).

v1 scope, real documented gaps:
- No alternate-key/shifted-key reporting (needs real keyboard-layout
  translation kitty gets from its own patched GLFW fork; stock GLFW, which
  puppy uses, doesn't expose this the same way).
- No text-embedding subfield.
- No hyper/meta modifiers -- stock GLFW's mod bitmask only has
  shift/control/alt/super/caps-lock/num-lock, no separate hyper/meta bits
  (those are a kitty-patched-GLFW extension). Always reported as unset.
- Plain printable keys use a simple ASCII-lowering of the GLFW key constant
  (GLFW_KEY_A=65 -> 'a'=97) rather than a real layout-aware base character
  (e.g. via glfw.get_key_name) -- correct for US/QWERTY-like layouts, a real
  approximation for others.
- Gating repeat/release events on the report_all_event_types flag: kitty's
  own C dispatch for this is entangled with its UI shortcut-handling layer,
  not meaningfully traceable in isolation for a terminal with no shortcut
  system. This module's interpretation (not independently re-verified
  against that entangled path): PRESS and REPEAT are always encoded
  (REPEAT identically to PRESS, no action subfield, unless the flag is set);
  RELEASE is only encoded when the flag is set. Matches the protocol's
  documented intent (clients that don't ask for release events shouldn't
  see them) even if the exact C code path wasn't independently confirmed.
"""
from __future__ import annotations

import glfw

_SHIFT, _ALT, _CTRL, _SUPER, _HYPER, _META, _CAPS_LOCK, _NUM_LOCK = 1, 2, 4, 8, 16, 32, 64, 128

# Progressive-enhancement flag bits (CSI = flags ; mode u), per the protocol.
FLAG_DISAMBIGUATE = 0b1
FLAG_REPORT_EVENT_TYPES = 0b10
FLAG_REPORT_ALTERNATE_KEYS = 0b100
FLAG_REPORT_ALL_KEYS = 0b1000
FLAG_REPORT_TEXT = 0b10000

_PRESS, _REPEAT, _RELEASE = 1, 2, 3  # wire action values (KeyAction+1)

# Functional-key wire codepoints, verified exact against kitty's own
# glfw-wrapper.h GLFW_FKEY_* enum (its protocol spec publishes these same
# values as the wire format directly).
_FUNCTIONAL_KEYS = {
    glfw.KEY_ESCAPE: 0xE000,
    glfw.KEY_ENTER: 0xE001,
    glfw.KEY_TAB: 0xE002,
    glfw.KEY_BACKSPACE: 0xE003,
    glfw.KEY_INSERT: 0xE004,
    glfw.KEY_DELETE: 0xE005,
    glfw.KEY_LEFT: 0xE006,
    glfw.KEY_RIGHT: 0xE007,
    glfw.KEY_UP: 0xE008,
    glfw.KEY_DOWN: 0xE009,
    glfw.KEY_PAGE_UP: 0xE00A,
    glfw.KEY_PAGE_DOWN: 0xE00B,
    glfw.KEY_HOME: 0xE00C,
    glfw.KEY_END: 0xE00D,
    glfw.KEY_F1: 0xE014,
    glfw.KEY_F2: 0xE015,
    glfw.KEY_F3: 0xE016,
    glfw.KEY_F4: 0xE017,
    glfw.KEY_F5: 0xE018,
    glfw.KEY_F6: 0xE019,
    glfw.KEY_F7: 0xE01A,
    glfw.KEY_F8: 0xE01B,
    glfw.KEY_F9: 0xE01C,
    glfw.KEY_F10: 0xE01D,
    glfw.KEY_F11: 0xE01E,
    glfw.KEY_F12: 0xE01F,
}


def _key_codepoint(key: int) -> int | None:
    if key in _FUNCTIONAL_KEYS:
        return _FUNCTIONAL_KEYS[key]
    if glfw.KEY_A <= key <= glfw.KEY_Z:
        return key - glfw.KEY_A + ord("a")
    if glfw.KEY_0 <= key <= glfw.KEY_9:
        return key  # GLFW_KEY_0..9 already equal ASCII '0'..'9'
    return None  # unmapped key, a real v1 gap (punctuation, non-US keys, etc.)


def _encode_mods(mods: int) -> int:
    value = 0
    if mods & glfw.MOD_SHIFT:
        value |= _SHIFT
    if mods & glfw.MOD_ALT:
        value |= _ALT
    if mods & glfw.MOD_CONTROL:
        value |= _CTRL
    if mods & glfw.MOD_SUPER:
        value |= _SUPER
    if mods & glfw.MOD_CAPS_LOCK:
        value |= _CAPS_LOCK
    if mods & glfw.MOD_NUM_LOCK:
        value |= _NUM_LOCK
    return value + 1


def encode_kitty_key_event(key: int, action: int, mods: int, flags: int) -> bytes | None:
    """action is glfw.PRESS/RELEASE/REPEAT. Returns None if this key has no
    mapped codepoint (a real v1 gap, see module docstring) or if this event
    is suppressed by the current flags (release without
    FLAG_REPORT_EVENT_TYPES, see module docstring's gating note).
    """
    codepoint = _key_codepoint(key)
    if codepoint is None:
        return None

    report_events = bool(flags & FLAG_REPORT_EVENT_TYPES)
    if action == glfw.RELEASE and not report_events:
        return None

    mods_value = _encode_mods(mods)
    add_action = report_events and action != glfw.PRESS
    has_mods = mods_value != 1

    parts = [str(codepoint)]
    if has_mods or add_action:
        second = str(mods_value)
        if add_action:
            wire_action = _RELEASE if action == glfw.RELEASE else (_REPEAT if action == glfw.REPEAT else _PRESS)
            second += f":{wire_action}"
        parts.append(second)
    return ("\x1b[" + ";".join(parts) + "u").encode("ascii")

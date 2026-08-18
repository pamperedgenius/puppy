import pytest

glfw = pytest.importorskip("glfw")

from puppy.kitty_keyboard import (
    FLAG_DISAMBIGUATE,
    FLAG_REPORT_EVENT_TYPES,
    encode_kitty_key_event,
)


def test_escape_press_no_mods():
    # 0xE000 = 57344, verified exact against kitty's real glfw-wrapper.h
    result = encode_kitty_key_event(glfw.KEY_ESCAPE, glfw.PRESS, 0, FLAG_DISAMBIGUATE)
    assert result == b"\x1b[57344u"


def test_up_arrow_with_ctrl():
    # 0xE008 = 57352, mods = CTRL(4)+1 = 5
    result = encode_kitty_key_event(glfw.KEY_UP, glfw.PRESS, glfw.MOD_CONTROL, FLAG_DISAMBIGUATE)
    assert result == b"\x1b[57352;5u"


def test_enter_with_shift():
    # 0xE001 = 57345, mods = SHIFT(1)+1 = 2
    result = encode_kitty_key_event(glfw.KEY_ENTER, glfw.PRESS, glfw.MOD_SHIFT, FLAG_DISAMBIGUATE)
    assert result == b"\x1b[57345;2u"


def test_plain_letter_press_no_mods():
    result = encode_kitty_key_event(glfw.KEY_A, glfw.PRESS, 0, FLAG_DISAMBIGUATE)
    assert result == b"\x1b[97u"  # 'a' = 97


def test_digit_key():
    result = encode_kitty_key_event(glfw.KEY_5, glfw.PRESS, 0, FLAG_DISAMBIGUATE)
    assert result == b"\x1b[53u"  # '5' = 53


def test_repeat_without_report_event_types_matches_press():
    press = encode_kitty_key_event(glfw.KEY_A, glfw.PRESS, 0, FLAG_DISAMBIGUATE)
    repeat = encode_kitty_key_event(glfw.KEY_A, glfw.REPEAT, 0, FLAG_DISAMBIGUATE)
    assert press == repeat == b"\x1b[97u"


def test_repeat_with_report_event_types_gets_action_subfield():
    flags = FLAG_DISAMBIGUATE | FLAG_REPORT_EVENT_TYPES
    result = encode_kitty_key_event(glfw.KEY_A, glfw.REPEAT, 0, flags)
    assert result == b"\x1b[97;1:2u"  # no-mods sentinel "1", action=2 (repeat)


def test_release_with_report_event_types_gets_action_subfield():
    flags = FLAG_DISAMBIGUATE | FLAG_REPORT_EVENT_TYPES
    result = encode_kitty_key_event(glfw.KEY_A, glfw.RELEASE, 0, flags)
    assert result == b"\x1b[97;1:3u"  # action=3 (release)


def test_release_without_report_event_types_is_suppressed():
    result = encode_kitty_key_event(glfw.KEY_A, glfw.RELEASE, 0, FLAG_DISAMBIGUATE)
    assert result is None


def test_unmapped_key_returns_none():
    result = encode_kitty_key_event(glfw.KEY_LEFT_SHIFT, glfw.PRESS, 0, FLAG_DISAMBIGUATE)
    assert result is None


def test_multiple_modifiers_combine():
    mods = glfw.MOD_SHIFT | glfw.MOD_CONTROL | glfw.MOD_ALT
    result = encode_kitty_key_event(glfw.KEY_UP, glfw.PRESS, mods, FLAG_DISAMBIGUATE)
    # SHIFT(1) + ALT(2) + CTRL(4) + 1 = 8
    assert result == b"\x1b[57352;8u"

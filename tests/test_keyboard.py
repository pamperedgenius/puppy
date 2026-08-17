import pytest

glfw = pytest.importorskip("glfw")

from puppy.keyboard import encode_char, encode_key


def test_arrow_keys_normal_mode():
    assert encode_key(glfw.KEY_UP, 0, decckm=False) == b"\x1b[A"
    assert encode_key(glfw.KEY_DOWN, 0, decckm=False) == b"\x1b[B"
    assert encode_key(glfw.KEY_RIGHT, 0, decckm=False) == b"\x1b[C"
    assert encode_key(glfw.KEY_LEFT, 0, decckm=False) == b"\x1b[D"


def test_arrow_keys_application_mode():
    assert encode_key(glfw.KEY_UP, 0, decckm=True) == b"\x1bOA"
    assert encode_key(glfw.KEY_LEFT, 0, decckm=True) == b"\x1bOD"


def test_fixed_navigation_keys():
    assert encode_key(glfw.KEY_HOME, 0) == b"\x1bOH"
    assert encode_key(glfw.KEY_END, 0) == b"\x1bOF"
    assert encode_key(glfw.KEY_DELETE, 0) == b"\x1b[3~"
    assert encode_key(glfw.KEY_INSERT, 0) == b"\x1b[2~"
    assert encode_key(glfw.KEY_PAGE_UP, 0) == b"\x1b[5~"
    assert encode_key(glfw.KEY_PAGE_DOWN, 0) == b"\x1b[6~"


def test_enter_backspace_tab_escape():
    assert encode_key(glfw.KEY_ENTER, 0) == b"\r"
    assert encode_key(glfw.KEY_BACKSPACE, 0) == b"\x7f"
    assert encode_key(glfw.KEY_TAB, 0) == b"\t"
    assert encode_key(glfw.KEY_ESCAPE, 0) == b"\x1b"


def test_function_keys():
    assert encode_key(glfw.KEY_F1, 0) == b"\x1bOP"
    assert encode_key(glfw.KEY_F5, 0) == b"\x1b[15~"
    assert encode_key(glfw.KEY_F12, 0) == b"\x1b[24~"


def test_ctrl_letter_produces_control_byte():
    assert encode_key(glfw.KEY_A, glfw.MOD_CONTROL) == bytes([1])
    assert encode_key(glfw.KEY_Z, glfw.MOD_CONTROL) == bytes([26])
    assert encode_key(glfw.KEY_C, glfw.MOD_CONTROL) == bytes([3])  # Ctrl+C = ETX


def test_letter_without_ctrl_produces_nothing_here():
    # plain letters are handled by encode_char (the GLFW char callback), not
    # encode_key -- must not double-send.
    assert encode_key(glfw.KEY_A, 0) is None


def test_unhandled_key_returns_none_not_raise():
    assert encode_key(glfw.KEY_LEFT_SHIFT, 0) is None
    assert encode_key(glfw.KEY_F13, 0) is None  # documented v1 gap (F13+)


def test_encode_char_ascii():
    assert encode_char(ord("a")) == b"a"


def test_encode_char_unicode():
    assert encode_char(ord("é")) == "é".encode("utf-8")

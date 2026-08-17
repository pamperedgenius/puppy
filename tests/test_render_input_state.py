import pytest

glfw = pytest.importorskip("glfw")

from puppy.render.input_state import InputState
from puppy.screen import Screen


class _StubFont:
    cell_width = 8
    cell_height = 16


class _StubSession:
    def __init__(self):
        self.writes = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)


@pytest.fixture
def state():
    screen = Screen(rows=5, cols=10)
    session = _StubSession()
    return InputState(session, screen, _StubFont())


def test_plain_char_is_written(state):
    state.on_char(ord("a"))
    assert state.session.writes == [b"a"]


def test_key_press_writes_legacy_sequence(state):
    state.on_key(glfw.KEY_UP, 0, glfw.PRESS, 0)
    assert state.session.writes == [b"\x1b[A"]


def test_key_repeat_also_writes(state):
    state.on_key(glfw.KEY_UP, 0, glfw.REPEAT, 0)
    assert state.session.writes == [b"\x1b[A"]


def test_key_release_writes_nothing(state):
    state.on_key(glfw.KEY_UP, 0, glfw.RELEASE, 0)
    assert state.session.writes == []


def test_key_respects_decckm_from_screen_private_modes(state):
    state.screen.set_private_mode(1, True)  # DECCKM on
    state.on_key(glfw.KEY_UP, 0, glfw.PRESS, 0)
    assert state.session.writes == [b"\x1bOA"]


def test_ctrl_c_sends_etx(state):
    state.on_key(glfw.KEY_C, 0, glfw.PRESS, glfw.MOD_CONTROL)
    assert state.session.writes == [bytes([3])]


def test_mouse_click_reports_only_when_mode_enabled(state):
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    assert state.session.writes == []  # no mouse mode enabled yet
    state.screen.set_private_mode(1000, True)
    state.screen.set_private_mode(1006, True)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    assert state.session.writes == [b"\x1b[<0;1;1M"]  # cell (0,0) -> wire (1,1)


def test_mouse_release_reports_and_clears_pressed_button(state):
    state.screen.set_private_mode(1000, True)
    state.screen.set_private_mode(1006, True)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.RELEASE, 0)
    assert state.pressed_button is None
    assert state.session.writes[-1] == b"\x1b[<0;1;1m"


def test_cursor_pos_updates_cell_and_reports_drag_when_button_held(state):
    state.screen.set_private_mode(1002, True)
    state.screen.set_private_mode(1006, True)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    state.session.writes.clear()
    state.on_cursor_pos(24.0, 33.0)  # col = 24//8 = 3, row = 33//16 = 2
    assert (state.last_col, state.last_row) == (3, 2)
    assert state.session.writes == [b"\x1b[<32;4;3M"]  # drag bit set (cb=32)


def test_cursor_pos_clamps_to_screen_bounds(state):
    state.on_cursor_pos(999999.0, -50.0)
    assert state.last_col == state.screen.cols - 1
    assert state.last_row == 0


def test_scroll_up_and_down(state):
    state.screen.set_private_mode(1000, True)
    state.screen.set_private_mode(1006, True)
    state.on_scroll(0, 1.0)
    assert state.session.writes[-1] == b"\x1b[<64;1;1M"
    state.on_scroll(0, -1.0)
    assert state.session.writes[-1] == b"\x1b[<65;1;1M"


def test_scroll_zero_offset_does_nothing(state):
    state.screen.set_private_mode(1000, True)
    state.screen.set_private_mode(1006, True)
    state.on_scroll(0, 0)
    assert state.session.writes == []

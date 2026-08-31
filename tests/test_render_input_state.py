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


def test_kitty_mode_routes_key_through_csi_u(state):
    state.screen.set_key_encoding_flags(0b1)  # FLAG_DISAMBIGUATE
    state.on_key(glfw.KEY_UP, 0, glfw.PRESS, 0)
    assert state.session.writes == [b"\x1b[57352u"]


def test_kitty_mode_suppresses_char_callback(state):
    state.screen.set_key_encoding_flags(0b1)
    state.on_char(ord("a"))
    assert state.session.writes == []


def test_kitty_mode_sends_release_only_when_flag_set(state):
    state.screen.set_key_encoding_flags(0b1)  # disambiguate only, no event-types
    state.on_key(glfw.KEY_UP, 0, glfw.RELEASE, 0)
    assert state.session.writes == []
    state.screen.set_key_encoding_flags(0b10, how=2)  # add report-event-types
    state.on_key(glfw.KEY_UP, 0, glfw.RELEASE, 0)
    assert state.session.writes == [b"\x1b[57352;1:3u"]


def test_legacy_mode_used_when_no_kitty_flags(state):
    assert state.screen.key_encoding_flags == 0
    state.on_key(glfw.KEY_UP, 0, glfw.PRESS, 0)
    assert state.session.writes == [b"\x1b[A"]  # legacy sequence, not CSI u


# --- text selection ---


@pytest.fixture
def state_with_clipboard():
    screen = Screen(rows=5, cols=10)
    session = _StubSession()
    copied = []
    return InputState(session, screen, _StubFont(), copy_to_clipboard=copied.append), copied


def test_left_click_without_mouse_mode_starts_selection_and_reports_nothing(state):
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    assert state.selecting is True
    assert state.screen.selection_start == (0, 0)  # last_col/last_row default to (0, 0)
    assert state.session.writes == []


def test_shift_click_forces_selection_even_with_mouse_mode_enabled(state):
    state.screen.set_private_mode(1000, True)
    state.screen.set_private_mode(1006, True)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, glfw.MOD_SHIFT)
    assert state.selecting is True
    assert state.session.writes == []  # not forwarded to the program


def test_click_with_mouse_mode_and_no_shift_reports_normally_not_selection():
    screen = Screen(rows=5, cols=10)
    screen.set_private_mode(1000, True)
    screen.set_private_mode(1006, True)
    session = _StubSession()
    state = InputState(session, screen, _StubFont())
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    assert state.selecting is False
    assert session.writes == [b"\x1b[<0;1;1M"]


def test_drag_updates_selection_and_release_copies_text():
    screen = Screen(rows=5, cols=10)
    for i, ch in enumerate("hello"):
        screen.grid[0][i].char = ch
    session = _StubSession()
    copied = []
    state = InputState(session, screen, _StubFont(), copy_to_clipboard=copied.append)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)  # presses at (0, 0)
    state.on_cursor_pos(32.0, 0.0)  # col = 32 // 8 = 4, row = 0
    assert screen.selection_end == (0, 4)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.RELEASE, 0)
    assert state.selecting is False
    assert copied == ["hello"]
    assert session.writes == []  # never forwarded as mouse reports


def test_release_without_drag_does_not_copy(state_with_clipboard):
    state, copied = state_with_clipboard
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.PRESS, 0)
    state.on_mouse_button(glfw.MOUSE_BUTTON_LEFT, glfw.RELEASE, 0)
    assert copied == []


def test_typing_clears_an_active_selection(state_with_clipboard):
    state, _ = state_with_clipboard
    state.screen.start_selection(0, 0)
    state.screen.update_selection(0, 3)
    assert state.screen.has_selection() is True
    state.on_key(glfw.KEY_A, 0, glfw.PRESS, 0)
    assert state.screen.has_selection() is False

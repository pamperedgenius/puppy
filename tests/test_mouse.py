from puppy.mouse import MouseAction, MouseButton, encode_sgr_mouse_event, generate_mouse_report
from puppy.screen import Screen


# Expected byte strings hand-derived from kitty's real bit constants
# (SHIFT=4, ALT=8, CTRL=16, MOTION=32, SCROLL=64, EXTRA=128) and button mapping
# (left/middle/right -> cb 0/1/2, scroll up/down -> cb 64/65), not guessed.

def test_left_press_top_left_no_modifiers():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.LEFT) == b"\x1b[<0;1;1M"


def test_left_release_keeps_real_button_not_forced_to_3():
    # Unlike legacy X10/urxvt protocols, SGR release reports which button was
    # released (same cb as the press), signaled by lowercase 'm' instead of 'M'.
    assert encode_sgr_mouse_event(MouseAction.RELEASE, 0, 0, MouseButton.LEFT) == b"\x1b[<0;1;1m"


def test_middle_and_right_press():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.MIDDLE) == b"\x1b[<1;1;1M"
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.RIGHT) == b"\x1b[<2;1;1M"


def test_scroll_up_and_down():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.SCROLL_UP) == b"\x1b[<64;1;1M"
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.SCROLL_DOWN) == b"\x1b[<65;1;1M"


def test_coordinates_are_1_indexed_on_the_wire():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 4, 9, MouseButton.LEFT) == b"\x1b[<0;10;5M"


def test_drag_sets_motion_bit_on_top_of_button():
    assert encode_sgr_mouse_event(MouseAction.DRAG, 0, 0, MouseButton.LEFT) == b"\x1b[<32;1;1M"


def test_pure_move_with_no_button_uses_cb_3_plus_motion():
    assert encode_sgr_mouse_event(MouseAction.MOVE, 0, 0, None) == b"\x1b[<35;1;1M"


def test_drag_with_no_button_produces_no_event():
    assert encode_sgr_mouse_event(MouseAction.DRAG, 0, 0, None) is None


def test_press_with_no_button_produces_no_event():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, None) is None


def test_modifiers_or_into_cb():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.LEFT, shift=True) == b"\x1b[<4;1;1M"
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.LEFT, alt=True) == b"\x1b[<8;1;1M"
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.LEFT, ctrl=True) == b"\x1b[<16;1;1M"


def test_all_modifiers_combine():
    result = encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, MouseButton.LEFT, shift=True, alt=True, ctrl=True)
    assert result == b"\x1b[<28;1;1M"  # 4 | 8 | 16 = 28


def test_raw_int_button_accepted_alongside_enum():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, 1) == encode_sgr_mouse_event(
        MouseAction.PRESS, 0, 0, MouseButton.LEFT
    )


def test_invalid_button_number_produces_no_event():
    assert encode_sgr_mouse_event(MouseAction.PRESS, 0, 0, 99) is None


# --- generate_mouse_report: mode gating ---

def test_no_report_without_sgr_mode_even_if_button_mode_set():
    s = Screen()
    s.set_private_mode(1000, True)
    assert generate_mouse_report(s, MouseAction.PRESS, 0, 0, MouseButton.LEFT) is None


def test_press_reported_under_plain_button_mode_1000():
    s = Screen()
    s.set_private_mode(1000, True)
    s.set_private_mode(1006, True)
    assert generate_mouse_report(s, MouseAction.PRESS, 0, 0, MouseButton.LEFT) == b"\x1b[<0;1;1M"


def test_drag_not_reported_under_plain_button_mode_1000():
    s = Screen()
    s.set_private_mode(1000, True)
    s.set_private_mode(1006, True)
    assert generate_mouse_report(s, MouseAction.DRAG, 0, 0, MouseButton.LEFT) is None


def test_drag_reported_under_1002():
    s = Screen()
    s.set_private_mode(1002, True)
    s.set_private_mode(1006, True)
    assert generate_mouse_report(s, MouseAction.DRAG, 0, 0, MouseButton.LEFT) == b"\x1b[<32;1;1M"


def test_pure_move_not_reported_under_1002():
    s = Screen()
    s.set_private_mode(1002, True)
    s.set_private_mode(1006, True)
    assert generate_mouse_report(s, MouseAction.MOVE, 0, 0, None) is None


def test_pure_move_reported_under_1003():
    s = Screen()
    s.set_private_mode(1003, True)
    s.set_private_mode(1006, True)
    assert generate_mouse_report(s, MouseAction.MOVE, 0, 0, None) == b"\x1b[<35;1;1M"


def test_no_report_when_no_mouse_mode_active_at_all():
    s = Screen()
    s.set_private_mode(1006, True)  # SGR alone, no button/drag/any-event mode
    assert generate_mouse_report(s, MouseAction.PRESS, 0, 0, MouseButton.LEFT) is None

from puppy.parser import Parser
from puppy.screen import Screen


def _run(data: bytes) -> Screen:
    screen = Screen(rows=5, cols=20)
    parser = Parser(screen)
    parser.feed(data)
    return screen


def test_plain_text():
    screen = _run(b"hello")
    assert screen.dump_text().splitlines()[0] == "hello"


def test_cursor_down_then_print():
    screen = _run(b"\x1b[2Bx")
    assert screen.cursor_row == 2
    assert screen.grid[2][0].char == "x"


def test_cursor_position_csi_H():
    screen = _run(b"\x1b[3;4Hy")
    assert screen.cursor_row == 2
    assert screen.cursor_col == 4
    assert screen.grid[2][3].char == "y"


def test_erase_display_mode_2_clears_everything():
    screen = _run(b"abc\x1b[2;1H\x1b[2J")
    assert all(line == "" for line in screen.dump_text().splitlines())


def test_sgr_bold_and_reset():
    screen = _run(b"\x1b[1ma\x1b[0mb")
    assert screen.grid[0][0].bold is True
    assert screen.grid[0][1].bold is False


def test_utf8_multibyte_char():
    screen = _run("héllo".encode("utf-8"))
    assert screen.grid[0][1].char == "é"


def test_private_mode_sequence_is_ignored_not_crashed():
    screen = _run(b"\x1b[?1049hok")
    assert screen.dump_text().splitlines()[0] == "ok"


def test_osc_sequence_terminated_by_bel_is_skipped():
    screen = _run(b"\x1b]0;title\x07ok")
    assert screen.dump_text().splitlines()[0] == "ok"


def test_osc_sequence_terminated_by_st_is_skipped():
    screen = _run(b"\x1b]0;title\x1b\\ok")
    assert screen.dump_text().splitlines()[0] == "ok"


def test_carriage_return_and_linefeed():
    screen = _run(b"ab\r\ncd")
    lines = screen.dump_text().splitlines()
    assert lines[0] == "ab"
    assert lines[1] == "cd"

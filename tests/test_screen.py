from puppy.screen import Screen


def test_put_char_advances_cursor():
    s = Screen(rows=3, cols=5)
    s.put_char("a")
    assert s.cursor_col == 1
    assert s.grid[0][0].char == "a"


def test_wrap_and_linefeed():
    s = Screen(rows=3, cols=2)
    s.put_char("a")
    s.put_char("b")
    s.put_char("c")  # column 2 is out of bounds -> wraps to next row
    assert s.cursor_row == 1
    assert s.cursor_col == 1
    assert s.grid[1][0].char == "c"


def test_cursor_position_clamps_to_bounds():
    s = Screen(rows=5, cols=5)
    s.cursor_position(100, 100)
    assert s.cursor_row == 4
    assert s.cursor_col == 4


def test_erase_in_line_mode2_clears_whole_line():
    s = Screen(rows=2, cols=3)
    for ch in "abc":
        s.put_char(ch)
    s.cursor_position(1, 2)
    s.erase_in_line(2)
    assert s.dump_text().splitlines()[0] == ""


def test_resize_preserves_overlap():
    s = Screen(rows=2, cols=2)
    s.put_char("x")
    s.resize(3, 3)
    assert s.grid[0][0].char == "x"
    assert s.rows == 3 and s.cols == 3


def test_sgr_bold_persists_until_reset():
    s = Screen(rows=1, cols=3)
    s.sgr([1])
    s.put_char("a")
    s.sgr([0])
    s.put_char("b")
    assert s.grid[0][0].bold is True
    assert s.grid[0][1].bold is False

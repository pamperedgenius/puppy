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


def test_reverse_index_scrolls_region_down_at_top():
    s = Screen(rows=3, cols=2)
    s.put_char("a")  # row 0
    s.cursor_position(2, 1)
    s.put_char("b")  # row 1
    s.cursor_position(1, 1)
    s.reverse_index()  # at scroll_top (row 0) -> scroll whole region down
    assert s.grid[0][0].char == " "
    assert s.grid[1][0].char == "a"
    assert s.grid[2][0].char == "b"


def test_alt_screen_enter_exit_round_trip():
    s = Screen(rows=2, cols=5)
    s.put_char("m")
    s.enter_alt_screen()
    assert s.dump_text().splitlines()[0] == ""
    s.put_char("a")
    s.exit_alt_screen()
    assert s.grid[0][0].char == "m"
    assert s.cursor_col == 1  # cursor position from before entering alt screen


def test_alt_screen_enter_is_idempotent():
    s = Screen(rows=2, cols=5)
    s.put_char("m")
    s.enter_alt_screen()
    s.put_char("a")
    s.enter_alt_screen()  # already active, must not stomp the saved main grid
    s.exit_alt_screen()
    assert s.grid[0][0].char == "m"


def test_set_scroll_region_narrows_and_linefeed_respects_it():
    s = Screen(rows=5, cols=2)
    s.set_scroll_region(2, 4)  # 1-indexed -> rows 1..3 (0-indexed)
    assert (s.scroll_top, s.scroll_bottom) == (1, 3)
    s.cursor_position(4, 1)  # bottom of the region
    s.put_char("a")
    s.cursor_position(4, 1)
    s.linefeed()  # at scroll_bottom -> scrolls only rows 1..3, not the whole screen
    assert s.grid[4][0].char == " "  # row below the region, untouched
    assert s.cursor_row == 3


def test_set_scroll_region_rejects_invalid_bounds():
    s = Screen(rows=5, cols=2)
    s.set_scroll_region(4, 2)  # top >= bottom, invalid
    assert (s.scroll_top, s.scroll_bottom) == (0, 4)  # unchanged (default full screen)


def test_set_scroll_region_huge_bottom_does_not_crash():
    s = Screen(rows=5, cols=2)
    s.set_scroll_region(1, 999999)
    assert (s.scroll_top, s.scroll_bottom) == (0, 4)  # invalid (out of range), ignored


def test_insert_lines_pushes_down_within_region_only():
    s = Screen(rows=4, cols=2)
    for i, ch in enumerate("abcd"):
        s.cursor_position(i + 1, 1)
        s.put_char(ch)
    s.cursor_position(2, 1)
    s.insert_lines(1)
    assert [row[0].char for row in s.grid] == ["a", " ", "b", "c"]


def test_delete_lines_pulls_up_within_region_only():
    s = Screen(rows=4, cols=2)
    for i, ch in enumerate("abcd"):
        s.cursor_position(i + 1, 1)
        s.put_char(ch)
    s.cursor_position(2, 1)
    s.delete_lines(1)
    assert [row[0].char for row in s.grid] == ["a", "c", "d", " "]


def test_insert_lines_huge_count_does_not_hang():
    s = Screen(rows=5, cols=2)
    s.insert_lines(999999)  # must clamp, not loop 999999 times
    assert len(s.grid) == 5


def test_insert_chars_shifts_line_right_and_truncates():
    s = Screen(rows=1, cols=5)
    for ch in "abcde":
        s.put_char(ch)
    s.cursor_position(1, 2)  # 0-indexed col 1
    s.insert_chars(2)
    line = [c.char for c in s.grid[0]]
    assert line == ["a", " ", " ", "b", "c"]
    assert len(line) == 5


def test_delete_chars_shifts_line_left_and_pads():
    s = Screen(rows=1, cols=5)
    for ch in "abcde":
        s.put_char(ch)
    s.cursor_position(1, 2)  # 0-indexed col 1
    s.delete_chars(2)
    line = [c.char for c in s.grid[0]]
    assert line == ["a", "d", "e", " ", " "]
    assert len(line) == 5


def test_delete_chars_huge_count_does_not_crash():
    s = Screen(rows=1, cols=5)
    for ch in "abcde":
        s.put_char(ch)
    s.cursor_position(1, 2)
    s.delete_chars(999999)
    line = [c.char for c in s.grid[0]]
    assert line == ["a", " ", " ", " ", " "]
    assert len(line) == 5


def test_sgr_256_color_indexed_fg_and_bg():
    s = Screen(rows=1, cols=3)
    s.sgr([38, 5, 196])
    s.sgr([48, 5, 22])
    s.put_char("a")
    assert s.grid[0][0].fg == 196
    assert s.grid[0][0].bg == 22


def test_sgr_truecolor_rgb_fg_and_bg():
    s = Screen(rows=1, cols=3)
    s.sgr([38, 2, 255, 0, 128])
    s.sgr([48, 2, 10, 20, 30])
    s.put_char("a")
    assert s.grid[0][0].fg == (255, 0, 128)
    assert s.grid[0][0].bg == (10, 20, 30)


def test_sgr_39_49_reset_extended_colors():
    s = Screen(rows=1, cols=3)
    s.sgr([38, 5, 196, 48, 2, 1, 2, 3])
    s.sgr([39, 49])
    s.put_char("a")
    assert s.grid[0][0].fg is None
    assert s.grid[0][0].bg is None


def test_sgr_truncated_extended_color_does_not_crash():
    s = Screen(rows=1, cols=3)
    s.sgr([38, 5])  # missing the color index -- malformed, must not raise
    s.put_char("a")
    assert s.grid[0][0].char == "a"


def test_top_of_screen_scroll_adds_to_scrollback():
    s = Screen(rows=2, cols=3)
    s.put_char("a")
    s.cursor_position(2, 1)
    s.linefeed()  # at scroll_bottom, top of region is row 0 -> scrolls into history
    assert s.scrollback_text() == "a"


def test_alt_screen_scroll_does_not_add_to_scrollback():
    s = Screen(rows=2, cols=3)
    s.enter_alt_screen()
    s.put_char("a")
    s.cursor_position(2, 1)
    s.linefeed()
    assert len(s.scrollback) == 0


def test_narrowed_scroll_region_does_not_add_to_scrollback():
    s = Screen(rows=4, cols=3)
    s.set_scroll_region(2, 4)  # top of region is row 1, not the screen top
    s.cursor_position(4, 1)
    s.put_char("a")
    s.cursor_position(4, 1)
    s.linefeed()
    assert len(s.scrollback) == 0


def test_delete_lines_does_not_add_to_scrollback():
    s = Screen(rows=3, cols=3)
    s.put_char("a")
    s.delete_lines(1)
    assert len(s.scrollback) == 0


def test_scrollback_is_bounded_by_limit():
    s = Screen(rows=2, cols=3, scrollback_limit=3)
    for i in range(10):
        s.cursor_position(2, 1)
        s.linefeed()
    assert len(s.scrollback) == 3


def test_erase_in_display_mode_3_clears_scrollback_mode_2_does_not():
    s = Screen(rows=2, cols=3)
    s.put_char("a")
    s.cursor_position(2, 1)
    s.linefeed()
    assert len(s.scrollback) == 1
    s.erase_in_display(2)
    assert len(s.scrollback) == 1  # mode 2 leaves scrollback alone
    s.erase_in_display(3)
    assert len(s.scrollback) == 0

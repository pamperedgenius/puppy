from puppy.screen import Cell, Screen


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


def test_set_scroll_region_clamps_huge_bottom_like_kitty():
    # kitty's real screen_set_margins (screen.c) clamps out-of-range bottom to the
    # screen height rather than rejecting the sequence -- ported that behavior
    # after checking against the actual source instead of assuming.
    s = Screen(rows=5, cols=2)
    s.set_scroll_region(1, 999999)
    assert (s.scroll_top, s.scroll_bottom) == (0, 4)


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


def test_insert_lines_and_delete_lines_do_carriage_return():
    # Verified against kitty's real screen_insert_lines/screen_delete_lines
    # (screen.c) -- both do a carriage return, unlike ICH/DCH.
    s = Screen(rows=3, cols=5)
    s.cursor_position(1, 3)
    s.insert_lines(1)
    assert s.cursor_col == 0
    s.cursor_position(1, 3)
    s.delete_lines(1)
    assert s.cursor_col == 0


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


def test_sgr_truecolor_with_explicit_colorspace_id():
    # ITU T.416's full form is 38:2:<colorspace-id>:r:g:b -- the colorspace id
    # comes before r/g/b, so the *last* 3 collected sub-params must be used, not
    # params[i+2:i+5]. is_subparam marks every entry after the leading 38 as a
    # ':'-chained sub-param, same as the real parser would for `38:2:0:255:0:128`.
    s = Screen(rows=1, cols=3)
    s.sgr([38, 2, 0, 255, 0, 128], is_subparam=[False, True, True, True, True, True])
    s.put_char("a")
    assert s.grid[0][0].fg == (255, 0, 128)


def test_sgr_truecolor_semicolon_form_unaffected_by_subparam_tracking():
    s = Screen(rows=1, cols=3)
    s.sgr([38, 2, 255, 0, 128], is_subparam=[False, False, False, False, False])
    s.put_char("a")
    assert s.grid[0][0].fg == (255, 0, 128)


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


def test_private_mode_tracking_set_and_reset():
    s = Screen()
    assert s.bracketed_paste is False
    s.set_private_mode(2004, True)
    assert s.bracketed_paste is True
    assert 2004 in s.private_modes
    s.set_private_mode(2004, False)
    assert s.bracketed_paste is False
    assert 2004 not in s.private_modes


def test_private_mode_convenience_properties():
    s = Screen()
    s.set_private_mode(1004, True)
    s.set_private_mode(2026, True)
    assert s.focus_tracking is True
    assert s.sync_output_pending is True
    assert s.bracketed_paste is False


def test_private_mode_reset_of_unset_mode_is_a_noop():
    s = Screen()
    s.set_private_mode(9999, False)  # never set -- must not raise
    assert 9999 not in s.private_modes


def test_cursor_defaults_to_visible_blinking_block():
    s = Screen()
    assert s.cursor_visible is True
    assert s.cursor_shape == "block"
    assert s.cursor_blink is True


def test_set_cursor_visible_toggles_dectcem():
    s = Screen()
    s.set_cursor_visible(False)
    assert s.cursor_visible is False
    s.set_cursor_visible(True)
    assert s.cursor_visible is True


def test_set_cursor_shape_decscusr_modes():
    # DECSCUSR: odd modes blink, even modes are steady (confirmed against
    # kitty's real screen_set_cursor: `blink = mode % 2`).
    s = Screen()
    s.set_cursor_shape(4)  # steady underline
    assert s.cursor_shape == "underline"
    assert s.cursor_blink is False
    s.set_cursor_shape(5)  # blinking beam
    assert s.cursor_shape == "beam"
    assert s.cursor_blink is True
    s.set_cursor_shape(2)  # steady block
    assert s.cursor_shape == "block"
    assert s.cursor_blink is False
    s.set_cursor_shape(7)  # no cursor shape at all
    assert s.cursor_shape == "none"


def test_set_cursor_shape_zero_resets_to_default_blinking_block():
    s = Screen()
    s.set_cursor_shape(4)  # steady underline
    s.set_cursor_shape(0)
    assert s.cursor_shape == "block"
    assert s.cursor_blink is True


def test_set_window_and_icon_title():
    s = Screen()
    s.set_window_title("window")
    s.set_icon_title("icon")
    assert s.window_title == "window"
    assert s.icon_title == "icon"


def test_set_palette_color_stores_raw_spec():
    s = Screen()
    s.set_palette_color(196, "rgb:ff/00/00")
    assert s.palette[196] == "rgb:ff/00/00"


def test_set_default_fg_bg():
    s = Screen()
    s.set_default_fg("#eeeeee")
    s.set_default_bg("#111111")
    assert s.default_fg_spec == "#eeeeee"
    assert s.default_bg_spec == "#111111"


def test_set_clipboard_stores_by_selection():
    s = Screen()
    s.set_clipboard("c", "aGVsbG8=")
    s.set_clipboard("p", "d29ybGQ=")
    assert s.clipboard == {"c": "aGVsbG8=", "p": "d29ybGQ="}


def test_set_hyperlink_attaches_and_clears():
    s = Screen(rows=1, cols=5)
    s.set_hyperlink("http://example.com")
    s.put_char("a")
    s.set_hyperlink(None)
    s.put_char("b")
    assert s.grid[0][0].hyperlink == "http://example.com"
    assert s.grid[0][1].hyperlink is None


# --- back-color-erase (bce): confirmed against kitty's real line_apply_cursor /
# linebuf_clear_lines (line.c/line-buf.c) -- ED/EL/ICH/DCH fill newly-blank cells
# with the *current* SGR state; IL/DL and a full alt-screen/resize blank do NOT.
# This asymmetry is real, verified in kitty's source, not an inconsistency to "fix".

def test_erase_in_line_applies_current_sgr_bce():
    s = Screen(rows=1, cols=3)
    s.sgr([41])  # red background -> normalized index 1 (see the sgr() color-index fix)
    s.erase_in_line(2)
    assert s.grid[0][0].bg == 1
    assert s.grid[0][0].char == " "


def test_erase_in_display_mode_2_applies_current_sgr_bce():
    s = Screen(rows=2, cols=2)
    s.sgr([44])  # blue background -> normalized index 4
    s.erase_in_display(2)
    assert all(cell.bg == 4 for row in s.grid for cell in row)


def test_erase_in_display_mode_3_applies_current_sgr_bce():
    s = Screen(rows=1, cols=2)
    s.sgr([44])
    s.erase_in_display(3)
    assert all(cell.bg == 4 for cell in s.grid[0])


def test_insert_chars_applies_current_sgr_bce():
    s = Screen(rows=1, cols=5)
    s.put_char("a")
    s.cursor_position(1, 1)
    s.sgr([42])  # green background -> normalized index 2
    s.insert_chars(2)
    assert s.grid[0][0].bg == 2
    assert s.grid[0][1].bg == 2


def test_delete_chars_applies_current_sgr_bce():
    s = Screen(rows=1, cols=5)
    for ch in "abcde":
        s.put_char(ch)
    s.cursor_position(1, 1)
    s.sgr([43])  # yellow background -> normalized index 3
    s.delete_chars(2)
    assert s.grid[0][4].bg == 3  # trailing exposed cell after the shift


def test_insert_lines_does_not_apply_bce():
    s = Screen(rows=3, cols=2)
    s.sgr([41])
    s.insert_lines(1)
    assert s.grid[0][0].bg is None


def test_delete_lines_does_not_apply_bce():
    s = Screen(rows=3, cols=2)
    s.sgr([41])
    s.delete_lines(1)
    assert s.grid[2][0].bg is None


def test_alt_screen_entry_does_not_apply_bce():
    s = Screen(rows=2, cols=2)
    s.sgr([41])
    s.enter_alt_screen()
    assert s.grid[0][0].bg is None


def test_resize_new_area_does_not_apply_bce():
    s = Screen(rows=1, cols=1)
    s.sgr([41])
    s.resize(2, 2)
    assert s.grid[1][1].bg is None


def test_bce_does_not_carry_hyperlink():
    s = Screen(rows=1, cols=3)
    s.set_hyperlink("http://example.com")
    s.sgr([41])
    s.erase_in_line(2)
    assert s.grid[0][0].bg == 1  # normalized index for red (41 - 40)
    assert s.grid[0][0].hyperlink is None


def test_key_encoding_flags_default_zero():
    s = Screen()
    assert s.key_encoding_flags == 0


def test_key_encoding_flags_set_directly():
    s = Screen()
    s.set_key_encoding_flags(0b11, how=1)
    assert s.key_encoding_flags == 0b11


def test_key_encoding_flags_or_in():
    s = Screen()
    s.set_key_encoding_flags(0b1, how=1)
    s.set_key_encoding_flags(0b10, how=2)
    assert s.key_encoding_flags == 0b11


def test_key_encoding_flags_and_not_remove():
    s = Screen()
    s.set_key_encoding_flags(0b111, how=1)
    s.set_key_encoding_flags(0b010, how=3)
    assert s.key_encoding_flags == 0b101


def test_key_encoding_flags_masked_to_7_bits():
    s = Screen()
    s.set_key_encoding_flags(0xFF, how=1)
    assert s.key_encoding_flags == 0x7F


def test_write_back_defaults_to_noop_when_none_given():
    s = Screen()
    s.report_key_encoding_flags()  # must not raise with no write_back configured


def test_report_key_encoding_flags_writes_correct_response():
    writes = []
    s = Screen(write_back=writes.append)
    s.set_key_encoding_flags(0b11)
    s.report_key_encoding_flags()
    assert writes == [b"\x1b[?3u"]


def test_report_key_encoding_flags_reflects_zero_by_default():
    writes = []
    s = Screen(write_back=writes.append)
    s.report_key_encoding_flags()
    assert writes == [b"\x1b[?0u"]


def test_report_primary_device_attributes_writes_correct_response():
    writes = []
    s = Screen(write_back=writes.append)
    s.report_primary_device_attributes()
    assert writes == [b"\x1b[?62;c"]


def test_report_secondary_device_attributes_writes_correct_response():
    writes = []
    s = Screen(write_back=writes.append)
    s.report_secondary_device_attributes()
    assert writes == [b"\x1b[>1;0;1c"]


def test_device_attribute_reports_default_to_noop_when_no_write_back_given():
    s = Screen()
    s.report_primary_device_attributes()  # must not raise
    s.report_secondary_device_attributes()  # must not raise


# --- text selection ---


def test_plain_click_with_no_drag_is_not_a_selection():
    s = Screen(rows=5, cols=10)
    s.start_selection(1, 2)
    assert s.has_selection() is False
    assert s.selected_text() == ""


def test_drag_selects_within_one_line():
    s = Screen(rows=5, cols=10)
    for i, ch in enumerate("hello world"[:10]):
        s.grid[1][i].char = ch
    s.start_selection(1, 0)
    s.update_selection(1, 4)
    assert s.has_selection() is True
    assert s.selected_text() == "hello"


def test_drag_selects_across_multiple_lines():
    s = Screen(rows=3, cols=5)
    for i, ch in enumerate("abcde"):
        s.grid[0][i].char = ch
    for i, ch in enumerate("fghij"):
        s.grid[1][i].char = ch
    s.start_selection(0, 3)
    s.update_selection(1, 1)
    assert s.selected_text() == "de\nfg"


def test_selection_normalizes_a_backward_drag():
    s = Screen(rows=5, cols=10)
    for i, ch in enumerate("hello"):
        s.grid[0][i].char = ch
    s.start_selection(0, 4)
    s.update_selection(0, 0)  # dragged right-to-left
    assert s.selected_text() == "hello"


def test_cell_selected_matches_selected_text_range():
    s = Screen(rows=3, cols=5)
    s.start_selection(0, 2)
    s.update_selection(1, 1)
    assert s.cell_selected(0, 1) is False
    assert s.cell_selected(0, 2) is True
    assert s.cell_selected(0, 4) is True
    assert s.cell_selected(1, 0) is True
    assert s.cell_selected(1, 1) is True
    assert s.cell_selected(1, 2) is False
    assert s.cell_selected(2, 0) is False


def test_clear_selection():
    s = Screen(rows=5, cols=10)
    s.start_selection(0, 0)
    s.update_selection(0, 3)
    s.clear_selection()
    assert s.has_selection() is False
    assert s.cell_selected(0, 1) is False


def test_resize_clears_a_stale_selection():
    s = Screen(rows=5, cols=10)
    s.start_selection(0, 0)
    s.update_selection(3, 8)
    s.resize(2, 4)  # old coordinates would now be out of range
    assert s.has_selection() is False


def test_alt_screen_enter_and_exit_clear_selection():
    s = Screen(rows=5, cols=10)
    s.start_selection(0, 0)
    s.update_selection(1, 1)
    s.enter_alt_screen()
    assert s.has_selection() is False
    s.start_selection(0, 0)
    s.update_selection(1, 1)
    s.exit_alt_screen()
    assert s.has_selection() is False


def test_mouse_reporting_active_requires_sgr_and_a_tracking_mode():
    s = Screen()
    assert s.mouse_reporting_active is False
    s.set_private_mode(1000, True)
    assert s.mouse_reporting_active is False  # no 1006 yet
    s.set_private_mode(1006, True)
    assert s.mouse_reporting_active is True
    s.set_private_mode(1000, False)
    assert s.mouse_reporting_active is False  # no tracking mode left


# --- scrollback view ---


def _fill_row(screen, row, text):
    for i, ch in enumerate(text):
        screen.grid[row][i].char = ch


def test_visible_rows_is_the_live_grid_at_zero_offset():
    s = Screen(rows=2, cols=5)
    _fill_row(s, 0, "aaaaa")
    _fill_row(s, 1, "bbbbb")
    assert s.visible_rows() is s.grid


def test_scroll_view_up_shows_scrollback_lines():
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    s.scrollback.append([Cell(char=ch) for ch in "one"])
    s.scrollback.append([Cell(char=ch) for ch in "two"])
    _fill_row(s, 0, "thr")
    _fill_row(s, 1, "liv")
    s.scroll_view(1)
    assert s.scrolled_back is True
    rows_text = ["".join(c.char for c in row).rstrip() for row in s.visible_rows()]
    assert rows_text == ["two", "thr"]
    s.scroll_view(1)
    rows_text = ["".join(c.char for c in row).rstrip() for row in s.visible_rows()]
    assert rows_text == ["one", "two"]


def test_scroll_view_clamped_to_available_history():
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    _fill_row(s, 0, "abc")
    s.cursor_position(2, 1)
    s.linefeed()  # one line of real scrollback
    s.scroll_view(50)
    assert s.scroll_offset == 1
    s.scroll_view(-50)
    assert s.scroll_offset == 0
    assert s.scrolled_back is False


def test_reset_scroll_view():
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    _fill_row(s, 0, "abc")
    s.cursor_position(2, 1)
    s.linefeed()
    s.scroll_view(1)
    s.reset_scroll_view()
    assert s.scroll_offset == 0
    assert s.visible_rows() is s.grid


def test_resize_resets_scroll_view():
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    _fill_row(s, 0, "abc")
    s.cursor_position(2, 1)
    s.linefeed()
    s.scroll_view(1)
    assert s.scroll_offset == 1
    s.resize(3, 3)
    assert s.scroll_offset == 0


def test_alt_screen_enter_and_exit_reset_scroll_view():
    # scroll_view() itself isn't alt-screen-aware (same design as
    # start_selection -- the real screen.in_alt_screen gate lives in
    # InputState, see test_render_input_state.py's
    # test_scroll_on_alt_screen_is_reported_not_local); this only checks
    # that the enter/exit transitions themselves reset any leftover offset.
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    _fill_row(s, 0, "abc")
    s.cursor_position(2, 1)
    s.linefeed()
    s.scroll_view(1)
    assert s.scroll_offset == 1
    s.enter_alt_screen()
    assert s.scroll_offset == 0
    s.exit_alt_screen()
    assert s.scroll_offset == 0


def test_visible_rows_reclamps_after_scrollback_shrinks():
    s = Screen(rows=2, cols=3, scrollback_limit=10)
    _fill_row(s, 0, "abc")
    s.cursor_position(2, 1)
    s.linefeed()
    s.scroll_view(1)
    s.erase_in_display(3)  # clears scrollback out from under an active scroll_offset
    rows = s.visible_rows()  # must not raise / index negatively
    assert len(rows) == 2


def test_in_alt_screen_property():
    s = Screen()
    assert s.in_alt_screen is False
    s.enter_alt_screen()
    assert s.in_alt_screen is True
    s.exit_alt_screen()
    assert s.in_alt_screen is False


# --- double/triple-click select ---


def test_select_word_selects_contiguous_word_characters():
    s = Screen(rows=1, cols=20)
    _fill_row(s, 0, "hello world.py-here")
    s.select_word(0, 2)  # inside "hello"
    assert s.selected_text() == "hello"


def test_select_word_includes_kittys_real_punctuation_set():
    s = Screen(rows=1, cols=20)
    _fill_row(s, 0, "world.py-here again")
    s.select_word(0, 2)  # inside "world.py-here", which includes '.' and '-'
    assert s.selected_text() == "world.py-here"


def test_select_word_on_whitespace_selects_nothing():
    s = Screen(rows=1, cols=10)
    _fill_row(s, 0, "abc   def")
    s.select_word(0, 4)  # a space
    assert s.has_selection() is False
    assert s.selected_text() == ""


def test_select_word_at_the_start_or_end_of_a_line():
    s = Screen(rows=1, cols=5)
    _fill_row(s, 0, "hello")
    s.select_word(0, 0)
    assert s.selected_text() == "hello"
    s.select_word(0, 4)
    assert s.selected_text() == "hello"


def test_select_line_selects_the_whole_row():
    s = Screen(rows=2, cols=10)
    _fill_row(s, 0, "hi")
    _fill_row(s, 1, "second row")
    s.select_line(0)
    assert s.selected_text() == "hi"  # trailing blanks rstripped
    s.select_line(1)
    assert s.selected_text() == "second row"

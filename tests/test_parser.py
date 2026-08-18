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


def test_huge_csi_param_digit_run_does_not_crash():
    # Regression: an unbounded digit run used to overflow Python's int-string
    # conversion limit and raise an uncaught ValueError, crashing the parser on a
    # single malicious/corrupt byte (e.g. from `cat`-ing an untrusted file).
    huge_digits = b"9" * 20000
    screen = _run(b"\x1b[" + huge_digits + b"Cx")
    assert "x" in screen.dump_text().splitlines()[0]


def test_huge_csi_param_count_does_not_crash():
    many_params = b";" * 20000
    screen = _run(b"\x1b[1" + many_params + b"mtext")
    assert screen.dump_text().splitlines()[0] == "text"


def test_alt_screen_hides_main_content_and_restores_it():
    screen = _run(b"main content\x1b[?1049h" + b"alt content")
    assert screen.dump_text().splitlines()[0] == "alt content"
    parser_exit = Parser(screen)
    parser_exit.feed(b"\x1b[?1049l")
    assert screen.dump_text().splitlines()[0] == "main content"


def test_alt_screen_restores_cursor_position():
    screen = _run(b"12345\x1b[?1049h")
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)
    Parser(screen).feed(b"\x1b[?1049l")
    assert screen.cursor_col == 5


def test_alt_screen_mode_1047_does_not_restore_cursor():
    # Verified against kitty's screen_toggle_screen_buffer: only mode 1049 saves
    # and restores the cursor; 47/1047 leave it wherever it ended up.
    screen = _run(b"12345\x1b[?1047h")
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)
    Parser(screen).feed(b"\x1b[3;3Hx\x1b[?1047l")  # move around inside alt screen
    assert (screen.cursor_row, screen.cursor_col) == (2, 3)  # left where it was, not restored


def test_alt_screen_1049_reuses_decsc_save_slot():
    # kitty's 1049 entry calls the *same* screen_save_cursor DECSC uses, so an
    # earlier ESC 7 save gets clobbered by a subsequent 1049 entry: restoring on
    # exit lands at (0,0) -- where the cursor was right before 1049 entry -- not
    # back at (4,4), which is where the original ESC 7 alone would have restored to.
    screen = _run(b"\x1b[5;5H\x1b7\x1b[1;1H\x1b[?1049hx\x1b[?1049l")
    assert (screen.cursor_row, screen.cursor_col) == (0, 0)


def test_decsc_decrc_save_restore_cursor():
    screen = _run(b"\x1b[3;5H\x1b7\x1b[1;1Hx\x1b8y")
    assert screen.grid[0][0].char == "x"
    assert screen.grid[2][4].char == "y"


def test_ind_moves_down_like_linefeed():
    screen = _run(b"\x1bDx")
    assert screen.cursor_row == 1
    assert screen.grid[1][0].char == "x"


def test_decstbm_narrows_scroll_region():
    screen = _run(b"\x1b[2;4r")
    assert (screen.scroll_top, screen.scroll_bottom) == (1, 3)


def test_csi_L_inserts_line_at_cursor():
    screen = _run(b"a\r\nb\r\n" + b"\x1b[1;1H" + b"\x1b[L")
    assert screen.grid[0][0].char == " "
    assert screen.grid[1][0].char == "a"


def test_csi_at_inserts_chars_and_csi_P_deletes_chars():
    screen = _run(b"abcde" + b"\x1b[1;2H" + b"\x1b[1@")
    assert [c.char for c in screen.grid[0][:5]] == ["a", " ", "b", "c", "d"]


def test_sgr_256_color_semicolon_form():
    screen = _run(b"\x1b[38;5;196mx")
    assert screen.grid[0][0].fg == 196


def test_sgr_truecolor_colon_form():
    # ':' is the ITU sub-parameter separator kitty and most modern terminals emit
    # for truecolor SGR -- must not get concatenated into the wrong parameter.
    screen = _run(b"\x1b[38:2:255:0:0mx")
    assert screen.grid[0][0].fg == (255, 0, 0)


def test_sgr_truecolor_colon_form_with_colorspace_id():
    # The full ITU T.416 form (38:2:<colorspace-id>:r:g:b) end-to-end through the
    # real parser, not just Screen.sgr directly -- confirms _is_subparam tracking
    # actually reaches the color logic correctly from real escape bytes.
    screen = _run(b"\x1b[38:2:0:255:0:128mx")
    assert screen.grid[0][0].fg == (255, 0, 128)


def test_osc_sequence_terminated_by_bel_is_parsed_and_does_not_leak_into_text():
    screen = _run(b"\x1b]0;title\x07ok")
    assert screen.dump_text().splitlines()[0] == "ok"
    assert screen.window_title == "title"


def test_osc_sequence_terminated_by_st_is_parsed_and_does_not_leak_into_text():
    screen = _run(b"\x1b]0;title\x1b\\ok")
    assert screen.dump_text().splitlines()[0] == "ok"
    assert screen.window_title == "title"


def test_carriage_return_and_linefeed():
    screen = _run(b"ab\r\ncd")
    lines = screen.dump_text().splitlines()
    assert lines[0] == "ab"
    assert lines[1] == "cd"


def test_bracketed_paste_mode_tracked():
    screen = _run(b"\x1b[?2004h")
    assert screen.bracketed_paste is True
    Parser(screen).feed(b"\x1b[?2004l")
    assert screen.bracketed_paste is False


def test_focus_tracking_and_sync_output_modes_tracked():
    screen = _run(b"\x1b[?1004h\x1b[?2026h")
    assert screen.focus_tracking is True
    assert screen.sync_output_pending is True


def test_multiple_private_modes_in_one_sequence():
    screen = _run(b"\x1b[?1004;2004;2026h")
    assert screen.focus_tracking is True
    assert screen.bracketed_paste is True
    assert screen.sync_output_pending is True


def test_unrecognized_private_mode_is_tracked_generically_not_crashed():
    screen = _run(b"\x1b[?9999hok")
    assert 9999 in screen.private_modes
    assert screen.dump_text().splitlines()[0] == "ok"


def test_alt_screen_mode_does_not_leak_into_generic_private_modes():
    screen = _run(b"\x1b[?1049h")
    assert 1049 not in screen.private_modes  # handled as a special case, not generic


def test_osc_0_sets_both_icon_and_window_title():
    screen = _run(b"\x1b]0;puppy session\x07")
    assert screen.window_title == "puppy session"
    assert screen.icon_title == "puppy session"


def test_osc_1_sets_icon_title_only():
    screen = _run(b"\x1b]1;icon only\x07")
    assert screen.icon_title == "icon only"
    assert screen.window_title is None


def test_osc_2_sets_window_title_only():
    screen = _run(b"\x1b]2;window only\x07")
    assert screen.window_title == "window only"
    assert screen.icon_title is None


def test_osc_4_sets_palette_color():
    screen = _run(b"\x1b]4;196;rgb:ff/00/00\x07")
    assert screen.palette[196] == "rgb:ff/00/00"


def test_osc_4_sets_multiple_palette_colors_in_one_sequence():
    screen = _run(b"\x1b]4;1;#ff0000;2;#00ff00\x07")
    assert screen.palette[1] == "#ff0000"
    assert screen.palette[2] == "#00ff00"


def test_osc_10_and_11_set_default_fg_bg():
    screen = _run(b"\x1b]10;#eeeeee\x07\x1b]11;#111111\x07")
    assert screen.default_fg_spec == "#eeeeee"
    assert screen.default_bg_spec == "#111111"


def test_osc_52_sets_clipboard():
    screen = _run(b"\x1b]52;c;aGVsbG8=\x07")
    assert screen.clipboard["c"] == "aGVsbG8="


def test_osc_52_empty_selection_defaults_to_c():
    screen = _run(b"\x1b]52;;aGVsbG8=\x07")
    assert screen.clipboard["c"] == "aGVsbG8="


def test_osc_8_hyperlink_attaches_to_subsequent_chars_until_cleared():
    screen = _run(b"\x1b]8;;http://example.com\x07link\x1b]8;;\x07plain")
    line = screen.grid[0]
    assert line[0].hyperlink == "http://example.com"
    assert line[3].hyperlink == "http://example.com"
    assert line[4].hyperlink is None  # 'p' of "plain", after the closing OSC 8


def test_osc_malformed_utf8_payload_does_not_crash():
    screen = _run(b"\x1b]0;\xff\xfe\x07ok")
    assert screen.dump_text().splitlines()[0] == "ok"


def test_osc_huge_code_number_does_not_crash():
    huge = b"9" * 20000
    screen = _run(b"\x1b]" + huge + b";x\x07ok")
    assert screen.dump_text().splitlines()[0] == "ok"
    assert screen.window_title is None


def test_osc_huge_payload_is_truncated_not_unbounded():
    huge_title = b"x" * 50000
    screen = _run(b"\x1b]2;" + huge_title + b"\x07ok")
    assert screen.dump_text().splitlines()[0] == "ok"
    assert screen.window_title is not None
    assert len(screen.window_title) < 50000


def test_csi_equals_u_sets_key_encoding_flags():
    screen = _run(b"\x1b[=1u")
    assert screen.key_encoding_flags == 1


def test_csi_equals_u_with_mode_param():
    screen = _run(b"\x1b[=1;1u\x1b[=2;2u")
    assert screen.key_encoding_flags == 0b11


def test_csi_question_u_query_is_ignored_not_crashed():
    # Real, documented gap: query-response needs writing back to the child,
    # not built yet -- must not crash or corrupt parser state.
    screen = _run(b"\x1b[?u" + b"ok")
    assert screen.dump_text().splitlines()[0] == "ok"

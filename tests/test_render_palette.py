from puppy.render.palette import ansi256_to_rgb, parse_color_spec, resolve_color


def test_basic_16_known_colors():
    assert ansi256_to_rgb(0) == (0, 0, 0)  # black
    assert ansi256_to_rgb(1) == (128, 0, 0)  # red
    assert ansi256_to_rgb(9) == (255, 0, 0)  # bright red
    assert ansi256_to_rgb(15) == (255, 255, 255)  # bright white


def test_color_cube_corners():
    assert ansi256_to_rgb(16) == (0, 0, 0)  # cube origin
    assert ansi256_to_rgb(231) == (255, 255, 255)  # cube far corner
    assert ansi256_to_rgb(196) == (255, 0, 0)  # a well-known "pure red" index


def test_grayscale_ramp_endpoints():
    assert ansi256_to_rgb(232) == (8, 8, 8)
    assert ansi256_to_rgb(255) == (238, 238, 238)


def test_ansi256_clamps_out_of_range_instead_of_crashing():
    assert ansi256_to_rgb(-5) == ansi256_to_rgb(0)
    assert ansi256_to_rgb(9999) == ansi256_to_rgb(255)


def test_parse_hex_spec():
    assert parse_color_spec("#ff0080") == (255, 0, 128)


def test_parse_rgb_colon_spec_two_digit():
    assert parse_color_spec("rgb:ff/00/80") == (255, 0, 128)


def test_parse_rgb_colon_spec_scales_different_digit_widths():
    # single-hex-digit component: f -> scaled from 0-15 to 0-255
    assert parse_color_spec("rgb:f/0/8") == (255, 0, round(8 * 255 / 15))


def test_parse_malformed_spec_returns_none_not_raise():
    assert parse_color_spec("not-a-color") is None
    assert parse_color_spec("#zzzzzz") is None
    assert parse_color_spec("rgb:ff/00") is None
    assert parse_color_spec("") is None


def test_resolve_color_none_uses_default():
    assert resolve_color(None, default=(1, 2, 3)) == (1, 2, 3)


def test_resolve_color_tuple_passes_through():
    assert resolve_color((10, 20, 30), default=(0, 0, 0)) == (10, 20, 30)


def test_resolve_color_int_uses_ansi256_table_by_default():
    assert resolve_color(1, default=(0, 0, 0)) == (128, 0, 0)


def test_resolve_color_prefers_palette_override():
    palette = {1: "#00ff00"}
    assert resolve_color(1, default=(0, 0, 0), palette=palette) == (0, 255, 0)


def test_resolve_color_falls_back_when_palette_entry_is_malformed():
    palette = {1: "garbage"}
    assert resolve_color(1, default=(0, 0, 0), palette=palette) == (128, 0, 0)


def test_resolve_color_falls_back_when_index_not_in_palette():
    palette = {5: "#00ff00"}  # doesn't cover index 1
    assert resolve_color(1, default=(0, 0, 0), palette=palette) == (128, 0, 0)

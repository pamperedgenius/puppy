import os

from puppy.render.theme import DEFAULT_BG, DEFAULT_FG, find_active_theme_dir, load_theme


def _make_theme_dir(tmp_path, name: str, colors: str) -> str:
    theme_dir = tmp_path / "themes" / name
    theme_dir.mkdir(parents=True)
    (theme_dir / "kitty-colors.conf").write_text(colors)
    (theme_dir / "wallpaper.png").write_bytes(b"")
    wallpaper_link = tmp_path / "wallpaper"
    os.symlink(theme_dir / "wallpaper.png", wallpaper_link)
    return str(wallpaper_link)


_SAMPLE_COLORS = """\
# comment line, must be skipped
background #000029
foreground #9FB6CD

color0   #000014
color1   #CD2626
color2   #66CD00
color3   #CDAD00
color4   #0000CD
color5   #CD96CD
color6   #336666
color7   #9FB6CD
color8   #445566
color9   #da5c5c
color10  #8cda40
color11  #dac240
color12  #4040da
color13  #dab0da
color14  #668c8c
color15  #ffffff
"""


def test_load_theme_parses_real_kitty_colors_format(tmp_path):
    link = _make_theme_dir(tmp_path, "midnight2", _SAMPLE_COLORS)
    theme = load_theme(link)
    assert theme.fg == (0x9F, 0xB6, 0xCD)
    assert theme.bg == (0x00, 0x00, 0x29)
    assert theme.ansi[0] == "#000014"
    assert theme.ansi[15] == "#ffffff"
    assert len(theme.ansi) == 16


def test_load_theme_parses_real_cursor_colors(tmp_path):
    colors = _SAMPLE_COLORS + "\ncursor #000000\ncursor_text_color #000029\n"
    link = _make_theme_dir(tmp_path, "midnight2", colors)
    theme = load_theme(link)
    assert theme.cursor == (0x00, 0x00, 0x00)
    assert theme.cursor_text_color == (0x00, 0x00, 0x29)


def test_load_theme_cursor_colors_fall_back_to_fg_and_bg(tmp_path):
    # _SAMPLE_COLORS has no cursor/cursor_text_color keys at all.
    link = _make_theme_dir(tmp_path, "midnight2", _SAMPLE_COLORS)
    theme = load_theme(link)
    assert theme.cursor == theme.fg
    assert theme.cursor_text_color == theme.bg


def test_load_theme_parses_real_selection_colors(tmp_path):
    colors = _SAMPLE_COLORS + "\nselection_foreground #fefefe\nselection_background #3c444b\n"
    link = _make_theme_dir(tmp_path, "focusedpanic", colors)
    theme = load_theme(link)
    assert theme.selection_fg == (0xFE, 0xFE, 0xFE)
    assert theme.selection_bg == (0x3C, 0x44, 0x4B)


def test_load_theme_selection_colors_fall_back_to_kitty_stock_defaults(tmp_path):
    # _SAMPLE_COLORS has no selection_foreground/selection_background keys.
    link = _make_theme_dir(tmp_path, "midnight2", _SAMPLE_COLORS)
    theme = load_theme(link)
    assert theme.selection_fg == (0, 0, 0)
    assert theme.selection_bg == (255, 250, 205)


def test_find_active_theme_dir_resolves_symlink(tmp_path):
    link = _make_theme_dir(tmp_path, "midnight2", _SAMPLE_COLORS)
    resolved = find_active_theme_dir(link)
    assert os.path.basename(resolved) == "midnight2"


def test_missing_wallpaper_link_falls_back_to_defaults(tmp_path):
    theme = load_theme(str(tmp_path / "does-not-exist"))
    assert theme.fg == DEFAULT_FG
    assert theme.bg == DEFAULT_BG
    assert theme.ansi == {}
    assert theme.cursor == DEFAULT_FG
    assert theme.cursor_text_color == DEFAULT_BG


def test_missing_kitty_colors_file_falls_back_to_defaults(tmp_path):
    theme_dir = tmp_path / "themes" / "bare"
    theme_dir.mkdir(parents=True)
    (theme_dir / "wallpaper.png").write_bytes(b"")
    link = tmp_path / "wallpaper"
    os.symlink(theme_dir / "wallpaper.png", link)

    theme = load_theme(str(link))
    assert theme.fg == DEFAULT_FG
    assert theme.bg == DEFAULT_BG


def test_partial_colors_only_fills_what_parses(tmp_path):
    link = _make_theme_dir(tmp_path, "partial", "background #111111\ncolor0 #222222\n")
    theme = load_theme(link)
    assert theme.bg == (0x11, 0x11, 0x11)
    assert theme.fg == DEFAULT_FG  # no foreground line -- falls back
    assert theme.ansi == {0: "#222222"}


def test_malformed_hex_values_are_skipped_not_crashed(tmp_path):
    link = _make_theme_dir(tmp_path, "bad", "background notahexcolor\ncolor0 #zzzzzz\ncolor1 #ff0000\n")
    theme = load_theme(link)
    assert theme.bg == DEFAULT_BG
    assert 0 not in theme.ansi
    assert theme.ansi[1] == "#ff0000"

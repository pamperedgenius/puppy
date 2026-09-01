from puppy.config import Config, DEFAULT_FONT_SIZE, load_config


def test_missing_config_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "does-not-exist.toml")
    assert config == Config()
    assert config.font_size == DEFAULT_FONT_SIZE
    assert config.font_family is None
    assert config.theme is None


def test_loads_real_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('font_size = 20\nfont_family = "JetBrains Mono"\ntheme = "vim-substrata"\n')
    config = load_config(path)
    assert config.font_size == 20
    assert config.font_family == "JetBrains Mono"
    assert config.theme == "vim-substrata"


def test_partial_config_fills_in_defaults_for_the_rest(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('font_size = 24\n')
    config = load_config(path)
    assert config.font_size == 24
    assert config.font_family is None
    assert config.theme is None


def test_malformed_toml_falls_back_to_defaults_not_a_crash(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('this is not [valid toml')
    config = load_config(path)
    assert config == Config()


def test_wrong_typed_values_are_ignored_not_trusted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('font_size = "big"\nfont_family = 5\ntheme = true\n')
    config = load_config(path)
    assert config.font_size == DEFAULT_FONT_SIZE
    assert config.font_family is None
    assert config.theme is None


def test_zero_or_negative_font_size_falls_back_to_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('font_size = 0\n')
    config = load_config(path)
    assert config.font_size == DEFAULT_FONT_SIZE

    path.write_text('font_size = -5\n')
    config = load_config(path)
    assert config.font_size == DEFAULT_FONT_SIZE


def test_empty_string_values_are_treated_as_unset(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('font_family = ""\ntheme = ""\n')
    config = load_config(path)
    assert config.font_family is None
    assert config.theme is None

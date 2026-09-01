"""Loads puppy's own config file, `~/.config/puppy/config.toml` (XDG
convention -- see RengeOS's own `~/CLAUDE.md` filesystem-placement rule,
same reasoning applied to this project's own config). This is unrelated to
RengeOS's theme-switcher config (`~/.config/theme-switcher/`) -- that's a
separate system this project only *reads from* (see render/theme.py); this
is the small set of values puppy itself lets a user override instead of
hardcoding in `render/app.py`'s `run()` defaults.

v1 scope, deliberately: `font_size`, `font_family`, and a theme-name
`theme` override only. Keybinds are a real, much bigger feature -- would
need to touch both `keyboard.py`'s legacy encoder and `kitty_keyboard.py`'s
protocol encoder, a materially larger change than this pass, not attempted
here. See PROGRESS.md.

Uses the stdlib `tomllib` (Python 3.11+, already this project's minimum --
see pyproject.toml) for parsing -- no new dependency needed. A missing or
malformed config file is never fatal: puppy should always still start with
its existing hardcoded defaults, same "falls back cleanly" convention
render/theme.py already follows for a missing/broken theme.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "puppy" / "config.toml"
DEFAULT_FONT_SIZE = 16


@dataclass
class Config:
    font_size: int = DEFAULT_FONT_SIZE
    font_family: str | None = None  # None = auto-resolve via fc-match "monospace"
    theme: str | None = None  # None = auto-detect RengeOS's currently active theme


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.is_file():
        return Config()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        # A broken config file shouldn't prevent puppy from starting at all --
        # same reasoning as theme.py falling back to plain defaults rather
        # than raising on a missing/malformed theme.
        return Config()

    font_size = data.get("font_size", DEFAULT_FONT_SIZE)
    if not isinstance(font_size, int) or font_size <= 0:
        font_size = DEFAULT_FONT_SIZE

    font_family = data.get("font_family")
    if not isinstance(font_family, str) or not font_family:
        font_family = None

    theme = data.get("theme")
    if not isinstance(theme, str) or not theme:
        theme = None

    return Config(font_size=font_size, font_family=font_family, theme=theme)

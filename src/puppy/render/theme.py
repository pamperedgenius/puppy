"""Reads RengeOS's active theme-switcher color scheme so puppy opens themed
instead of hardcoded white-on-black.

Every theme-switcher theme already ships a `kitty-colors.conf` (real kitty
theme format -- confirmed against `~/.config/theme-switcher/themes/midnight2/
kitty-colors.conf`: plain `key value` pairs, `background`/`foreground`/
`color0`-`color15`, `#`-prefixed full-line comments). Parsing that instead of
inventing a puppy-specific format means every existing theme "just works"
with zero per-theme puppy-specific work, matching how every other themed app
in this system (xtmux, OdyTTY, kitty itself) consumes theme-switcher output.

Active-theme discovery resolves `~/.config/theme-switcher/wallpaper` (a
per-theme symlink theme-set.sh regenerates on every switch) rather than
theme-set.sh's own real `current_theme()` mechanism (which resolves via the
waybar profile's `theme.css` symlink) -- both point at the same active
theme directory, but the wallpaper symlink doesn't require waybar to be
installed/running, so it's a lighter dependency for puppy to take.

The loaded colors feed into puppy the same way a program setting its own
theme via OSC 4 would: `Screen.set_palette_color(0..15, spec)` for the
ANSI colors (reusing the existing, already-tested OSC-4-override mechanism
in `puppy.render.palette.resolve_color` rather than adding a second color
path), and `DEFAULT_FG`/`DEFAULT_BG` in app.py for cells with no explicit
SGR color set.

Not a puppy-specific config format, not part of the kitty-protocol-parity
goal -- purely a RengeOS integration convenience. Falls back to plain
white-on-black (the pre-existing hardcoded default) if theme-switcher isn't
present at all, so puppy stays usable outside this one machine.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_FG = (255, 255, 255)
DEFAULT_BG = (0, 0, 0)

_DEFAULT_WALLPAPER_LINK = os.path.expanduser("~/.config/theme-switcher/wallpaper")


@dataclass
class Theme:
    fg: tuple[int, int, int] = DEFAULT_FG
    bg: tuple[int, int, int] = DEFAULT_BG
    # index (0-15) -> "#rrggbb", ready to hand straight to Screen.set_palette_color
    ansi: dict[int, str] = field(default_factory=dict)


def _parse_hex(spec: str) -> tuple[int, int, int] | None:
    spec = spec.strip()
    if not spec.startswith("#") or len(spec) != 7:
        return None
    try:
        return (int(spec[1:3], 16), int(spec[3:5], 16), int(spec[5:7], 16))
    except ValueError:
        return None


def _parse_kitty_colors(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            key, value = parts
            values[key] = value.strip()
    return values


def find_active_theme_dir(wallpaper_link: str = _DEFAULT_WALLPAPER_LINK) -> str | None:
    if not os.path.islink(wallpaper_link) and not os.path.exists(wallpaper_link):
        return None
    try:
        real = os.path.realpath(wallpaper_link)
    except OSError:
        return None
    return os.path.dirname(real)


def load_theme(wallpaper_link: str = _DEFAULT_WALLPAPER_LINK) -> Theme:
    theme_dir = find_active_theme_dir(wallpaper_link)
    if theme_dir is None:
        return Theme()
    colors_path = os.path.join(theme_dir, "kitty-colors.conf")
    if not os.path.isfile(colors_path):
        return Theme()
    values = _parse_kitty_colors(colors_path)
    fg = _parse_hex(values.get("foreground", "")) or DEFAULT_FG
    bg = _parse_hex(values.get("background", "")) or DEFAULT_BG
    ansi: dict[int, str] = {}
    for i in range(16):
        spec = values.get(f"color{i}")
        if spec is not None and _parse_hex(spec) is not None:
            ansi[i] = spec
    return Theme(fg=fg, bg=bg, ansi=ansi)

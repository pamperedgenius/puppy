"""Resolves a Cell.fg/bg value (0-255 palette index, an (r,g,b) truecolor
tuple, or None for "default") into concrete sRGB bytes.

- 0-15: the 16 basic colors. Uses the classic VGA/CGA default palette (what
  most real terminals start with before any theme/OSC 4 override).
- 16-231: the standard 6x6x6 xterm color cube.
- 232-255: the standard 24-step xterm grayscale ramp.

`Screen.palette` (OSC 4 overrides) stores raw spec strings ("#rrggbb" or
"rgb:rr/gg/bb"), not parsed colors, deliberately -- see screen.py's OSC
handling. `parse_color_spec` does that parsing here, where a color is
actually being resolved for rendering, and `resolve_color` prefers a palette
override over the built-in table when one exists and parses successfully.
"""
from __future__ import annotations

_BASIC_16 = [
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
]

_CUBE_STEPS = (0, 95, 135, 175, 215, 255)


def ansi256_to_rgb(index: int) -> tuple[int, int, int]:
    index = max(0, min(255, index))
    if index < 16:
        return _BASIC_16[index]
    if index < 232:
        i = index - 16
        r, g, b = i // 36, (i % 36) // 6, i % 6
        return (_CUBE_STEPS[r], _CUBE_STEPS[g], _CUBE_STEPS[b])
    level = 8 + (index - 232) * 10
    return (level, level, level)


def _scale_hex_component(digits: str) -> int:
    value = int(digits, 16)
    max_value = (16 ** len(digits)) - 1
    return round(value * 255 / max_value)


def parse_color_spec(spec: str) -> tuple[int, int, int] | None:
    """Parses "#rrggbb" or the X11-style "rgb:rr/gg/bb" (each component
    1-4 hex digits, scaled to 8-bit) into an (r, g, b) byte tuple. Returns
    None for anything else -- malformed/unrecognized specs, never raises.
    """
    spec = spec.strip()
    if spec.startswith("#") and len(spec) == 7:
        try:
            return (int(spec[1:3], 16), int(spec[3:5], 16), int(spec[5:7], 16))
        except ValueError:
            return None
    if spec.startswith("rgb:"):
        parts = spec[4:].split("/")
        if len(parts) != 3 or not all(parts):
            return None
        try:
            return tuple(_scale_hex_component(p) for p in parts)
        except ValueError:
            return None
    return None


def resolve_color(
    color: int | tuple[int, int, int] | None,
    default: tuple[int, int, int],
    palette: dict[int, str] | None = None,
) -> tuple[int, int, int]:
    if color is None:
        return default
    if isinstance(color, tuple):
        return color
    if palette:
        spec = palette.get(color)
        if spec is not None:
            parsed = parse_color_spec(spec)
            if parsed is not None:
                return parsed
    return ansi256_to_rgb(color)

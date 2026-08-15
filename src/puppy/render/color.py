"""sRGB <-> linear color conversion for the GPU renderer.

Theme colors, SGR RGB values, and hex codes are always specified as sRGB bytes
(the standard display convention -- what a color picker or `#rrggbb` means).
wgpu's clear_value/vertex-color inputs are linear-space and get gamma-*encoded*
by the hardware on write to an sRGB surface format (confirmed by round-tripping
clear+readback against the real GPU here, exact match across 0-255 -- see
render/gpu.py's GpuContext.create). So any sRGB byte must be decoded to linear
with srgb_to_linear before reaching the GPU, or it renders too bright/washed out.
"""
from __future__ import annotations


def srgb_to_linear(byte: int) -> float:
    """Decodes one sRGB-encoded channel byte (0-255) to a linear float (0.0-1.0),
    per IEC 61966-2-1. This is the value to hand to wgpu clear_value/vertex
    colors so the hardware's automatic linear->sRGB re-encode on write
    reproduces the original byte on screen.
    """
    c = byte / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def srgb_color(r: int, g: int, b: int, a: int = 255) -> tuple[float, float, float, float]:
    """Convenience: decode an (r, g, b[, a]) sRGB byte tuple to a linear
    (r, g, b, a) float tuple ready for wgpu clear_value/vertex colors. Alpha is
    NOT gamma-decoded (alpha is linear by convention, not a display color).
    """
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), a / 255.0)

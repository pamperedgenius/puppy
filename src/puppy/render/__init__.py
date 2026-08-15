"""Rendering layer: GPU setup, a live GLFW window, and text shaping/rasterization.

Toolkit choice (glfw + wgpu-py/rendercanvas + uharfbuzz + freetype-py) decided
2026-08-16 after comparing kitty's real stack (GLFW + OpenGL, confirmed from its
own source) and OdyTTY's real stack (winit + wgpu + swash/skrifa, confirmed from
its actual Cargo.toml on GitHub) against candidate Python bindings. See
PROGRESS.md's Architecture decisions log for the full reasoning -- short version:
this is the Python-native equivalent of what both locally-installed reference
terminals independently converged on (thin windowing + a modern GPU API + real
font-shaping libraries), not a full toolkit like GTK4.
"""

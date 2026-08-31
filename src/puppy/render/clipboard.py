"""Copies a terminal-native text selection to the system clipboard.

Two independent, both best-effort targets:
- CLIPBOARD via GLFW's own cross-platform clipboard API
  (glfwSetClipboardString) -- portable off this one machine, matching the
  windowing toolkit already chosen (see PROGRESS.md's Architecture decisions
  log). This is what a real Ctrl+V paste in most apps reads from.
- PRIMARY selection via `wl-copy -p` (Wayland-specific, a RengeOS-machine
  convenience, silently skipped if the binary is missing or the call fails)
  -- matches the X11/Wayland convention every other terminal on this system
  (xfce4-terminal, OdyTTY) follows: a mouse-drag selection is immediately
  available for middle-click paste, independent of and before any explicit
  copy action. GLFW has no PRIMARY-selection concept at all (Windows/macOS
  don't have one), so this can't go through the same portable path.
"""
from __future__ import annotations

import subprocess

import glfw


def copy_selection(window_handle, text: str) -> None:
    if not text:
        return
    glfw.set_clipboard_string(window_handle, text)
    try:
        subprocess.run(["wl-copy", "-p"], input=text.encode("utf-8"), timeout=1, check=False)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

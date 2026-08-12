# puppy — Progress, Plan & Decisions

Python terminal emulator built from scratch, targeting full kitty-protocol parity
(keyboard + graphics + extensions) plus baseline VT100/xterm/ECMA-48 compatibility.

**This file is the source of truth for the project.** Read it fully at the start of any
session before touching code — don't rely on conversation history, it can be lost.
Update it (status, decisions, next steps) as part of every work session, not just at the
end. Treat it the way `unified.md` is treated for the rest of RengeOS: replace stale
entries, don't append duplicates.

Background research (protocol specs, terminfo.dev, kitty C-source file map, layered
build plan): `~/Documents/python-terminal-emulator-research.md`. That file doesn't
change often — this one does.

## Current status (2026-08-13)

Just started. First slice exists: PTY spawn, a byte-level VT/ANSI parser, and an
in-memory screen buffer (grid of cells with cursor + basic SGR attributes). No rendering
yet — see "Deferred decision" below. Not yet tested against a live PTY in this
environment (see Next steps).

## Architecture decisions log

- **Rendering/windowing toolkit: DEFERRED, not yet decided.** Candidates from the
  research doc: `pywayland` (native Niri fit, most raw-event control), GTK4 (rich
  key-event API, heavier dep), SDL2 (simple, good raw key events, less native Wayland
  polish). This matters most for the kitty keyboard protocol, which needs real
  press/repeat/release key events, not just text input. **Deliberately scoped around**
  for now: the parser + screen-buffer core doesn't need a display to be built or tested,
  so we're building and proving that layer first and picking the toolkit when rendering
  actually starts (see Milestones).
- **Parser performance: pure Python first.** No Cython/Rust until profiling under real
  load (e.g. `cat` on a large file, `yes`) shows the pure-Python parser is actually the
  bottleneck. Don't pre-optimize.
- **PTY handling: stdlib `pty.fork()`**, not `os.forkpty()` + manual exec wiring — same
  end result, less boilerplate. Revisit only if `pty.fork()`'s implicit exec behavior
  becomes limiting.
- **Package layout: `src/puppy/` layout with a real `pyproject.toml`**, not a flat
  script — this project is meant to grow, and RengeOS convention is
  `~/Projects/<name>` for anything beyond a one-off script.
- **First testable milestone deliberately excludes rendering.** `python -m puppy` is a
  pass-through PTY proxy: your real stdin goes to the child shell, the child's raw
  output is mirrored to your real stdout (so it behaves like a normal shell session to
  use), *and* every byte is fed in parallel through the parser into the `Screen` model.
  A debug command dumps the `Screen`'s internal grid as text so you can compare it
  against what actually printed. This proves PTY + parser + screen-model correctness
  without needing to solve rendering first.

## Milestones

Layering follows the research doc's plan. Check off as completed; add a one-line note
with the date when something is confirmed working (not just "code exists").

- [x] Project scaffold + this progress file
- [x] `PtySession` — spawn `$SHELL` in a real PTY, read/write, resize via `TIOCSWINSZ`
- [x] `Parser` — byte-level state machine: C0 controls, ESC, CSI (with param
      collection + `?` private-mode prefix tolerance), OSC (string-terminated,
      contents currently discarded), SGR dispatch into `Screen`
- [x] `Screen` — grid of `Cell(char, fg, bg, bold, underline, reverse)`, cursor
      row/col, `CUU/CUD/CUF/CUB/CUP`, `ED`/`EL`, resize, plain-text dump
- [x] `python -m puppy` pass-through+dump harness (Ctrl+] triggers a screen dump to
      stderr so it doesn't corrupt the live session)
- [x] Unit tests for parser (cursor movement, SGR parsing, ED/EL) and screen
      (grid mutation, resize) — pure logic, no real PTY needed, run in CI/sandbox
- [ ] **Next up**: live-test `python -m puppy` in a real terminal window — spawn a
      shell, run a few commands (`ls`, `printf` with colors, `vim` briefly), confirm
      the mirrored output looks like a normal shell and the dump command's grid
      matches. This needs an actual terminal window, can't be done headlessly.
- [ ] Alternate screen buffer (mode 1049) + scrollback
- [ ] Scroll regions (DECSTBM), insert/delete line/char
- [ ] 256-color and 24-bit truecolor SGR (currently only basic 16-color SGR)
- [ ] Bracketed paste (2004), focus reporting (1004), synchronized output (2026)
- [ ] Mouse protocols (1000/1002/1003/1006 SGR)
- [ ] OSC family: title (0/1/2), palette (4/10/11), clipboard (52), hyperlinks (8)
- [ ] Terminfo entry so real programs (vim, ncurses apps) detect capabilities correctly
- [ ] **Decide rendering/windowing toolkit**, build minimal glyph-grid renderer
- [ ] Kitty keyboard protocol (CSI u) — needs the windowing toolkit decided first
- [ ] Kitty graphics protocol (RGB/RGBA/PNG direct mode first)
- [ ] Kitty graphics: unicode-placeholder image-in-text method
- [ ] Sixel graphics (fallback/parity with non-kitty terminals)
- [ ] Remaining kitty misc extensions (text-sizing, DnD, multi-cursor, file-transfer,
      notifications, pointer-shapes, color-stack, DECCARA)

## File map

```
puppy/
  PROGRESS.md          this file
  pyproject.toml
  src/puppy/
    __init__.py
    __main__.py         pass-through+dump harness (current entry point)
    pty_session.py       PtySession — spawn/read/write/resize a real PTY
    parser.py            Parser — byte-level VT/ANSI state machine
    screen.py             Screen, Cell — in-memory grid + cursor + SGR attrs
  tests/
    test_parser.py
    test_screen.py
```

## Next steps (pick up here)

1. Live-test `python -m puppy` in an actual terminal window (xfterm/OdyTTY/etc.) —
   this needs a real interactive session, not something to run headlessly. Per the
   live-window-testing rule, don't repeatedly spawn/close windows to test this —
   the user should run it and report back, or explicitly hand over a window to test in.
2. Once pass-through is confirmed sound, start on alternate-screen + scroll regions
   (next unchecked milestone) — still no rendering needed.
3. Revisit the windowing-toolkit decision once the text-only model feels solid enough
   that rendering is the next real thing blocking progress.

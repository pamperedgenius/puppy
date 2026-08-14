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

**Repo**: https://github.com/pamperedgenius/puppy (public, matches the visibility used
for other original from-scratch tools like `bane`/`python-sprint`/`startpage-for-me` —
forks of others' code are the ones kept private). **Workflow rule: push to GitHub at the
end of every work session** (not mid-session) so progress is never sitting only on this
machine — a placeholder push happened 2026-08-13 even though the session wasn't over,
per explicit request.

## Kitty-source verification pass (2026-08-15)

Prompted by a direct question ("have you actually been following kitty's protocols?").
Answer at the time: baseline VT100/xterm sequences had been implemented from spec
knowledge, not cross-checked line-by-line against kitty's real C source — worth being
honest about that gap rather than just asserting compliance. Did an actual verification
pass against `~/Projects/kitty/kitty/{screen,vt-parser}.c` and found four real,
now-fixed divergences:

1. **DECSTBM out-of-range bounds**: was rejecting the whole sequence; kitty's real
   `screen_set_margins` clamps top/bottom to the screen height instead. Fixed to clamp.
2. **SGR truecolor colorspace-id form** (`38:2:<colorspace>:r:g:b`): was assumed a known
   gap; confirmed real via kitty's `select_graphic_rendition`, which tracks `:` vs `;`
   sub-parameter boundaries (`is_sub_param`) and collects however many colon-chained
   values follow rather than assuming a fixed count. Ported the same idea: `Parser` now
   tracks `_is_subparam` alongside `_params`, `Screen.sgr` takes it and uses "last 3
   collected values are r/g/b" (correct per ITU T.416 field order, colorspace-id always
   comes first) instead of a hardcoded `params[i+2:i+5]` read.
3. **IL/DL do a carriage return**: kitty's `screen_insert_lines`/`screen_delete_lines`
   both call `screen_carriage_return` after shifting; `ICH`/`DCH` don't. Wasn't in any
   spec text consulted earlier, only in the actual source. Fixed.
4. **Alt-screen cursor handling differs by mode**: kitty's `screen_toggle_screen_buffer`
   only saves/restores the cursor for mode `1049` — modes `47`/`1047` toggle the buffer
   without touching cursor state at all. It also reuses the *same* save slot as
   `DECSC`/`DECRC` (calls `screen_save_cursor` directly), not a separate one, so a
   `DECSC` right before a `1049` entry gets clobbered by it on a real terminal. Was
   treating all three modes identically with an independent save slot; fixed both.

**Lesson**: spec-knowledge implementations of "obscure-looking" behavior (exact
clamping rules, whether an operation also does a CR, which private modes share state)
should be spot-checked against the real source when it's sitting right there in
`~/Projects/kitty`, not trusted from memory — none of these four were something a
plain reading of ECMA-48/xterm docs would have caught. Worth another pass like this
periodically, not just once.

## Current status (2026-08-15)

First slice done and pushed: `PtySession` (real PTY spawn/read/write/resize), `Parser`
(byte-level VT100/ECMA-48 state machine), `Screen` (grid of cells, cursor, basic SGR).
Since the initial push, added: alternate screen buffer (DECSET 47/1047/1049 — used by
vim/less/htop), DECSC/DECRC cursor save-restore (ESC 7/8), IND/RI (ESC D/M), DECSTBM
(`CSI r`, narrows `scroll_top`/`scroll_bottom`, clamped/validated against garbage
input), and insert/delete line/char (IL/DL/ICH/DCH — `CSI L/M/@/P`), all of it
scroll-region-aware, and 256-color/truecolor SGR (`38/48;5;n` and `38/48;2;r;g;b`) —
which surfaced a real parser gap while verifying it: the CSI state machine never handled
`:` (the ITU T.416 sub-parameter separator that kitty and most modern terminals actually
emit for truecolor, e.g. `38:2:255:0:0`), so colon digits were silently concatenated
onto the wrong parameter, corrupting color parsing. Fixed by treating `:` the same as
`;` at accumulation time (the rarer `38:2:<colorspace>:r:g:b` colorspace-id form was
fixed properly in the kitty-source verification pass above, not left as a gap). Also
added scrollback: a `deque(maxlen=2000)` history that only captures lines scrolled off
the *real* top of the *main* screen (not alt-screen scrolling, not a narrowed-DECSTBM
region's internal scrolling, not DL) — matches real terminal behavior on what counts as
"history". `CSI 3 J` (what `clear` actually sends) wipes it, `CSI 2 J` doesn't. 55 unit
tests passing, including hang-safety tests (`insert_lines`/`delete_chars` with a huge
count must clamp to the region/line size, not loop attacker-controlled-times — same DoS
class as the CSI-param fix below, caught proactively this time instead of by review).
`python -m puppy` was spawned in a live xfce4-terminal window (2026-08-15) and the user
confirmed it looked fine ("everything is good") — this counts as the live-test milestone
being done, even though nothing was independently screenshotted (no way to see a live GUI
window from here). Also ran
one ad hoc end-to-end smoke test in this environment —
spawned a real `/bin/sh` via `PtySession`, piped output through `Parser` into `Screen`,
confirmed real shell output flows through the whole pipeline correctly. That test also
showed the expected mess from *local echo* duplicating bytes (the shell echoes the typed
command line **and** the command's own output both land on the same PTY master stream) —
not a parser bug, just a reminder that `python -m puppy` (which runs the terminal in raw
mode, no double-echo) is the real way to validate this, not ad hoc scripting. No
rendering yet — see the deferred decision below. `python -m puppy` itself has **not**
been live-tested yet (needs a real interactive terminal window, see Next steps).

**Security fix (2026-08-13, caught by an automated commit review, not by us):** `_csi()`
accumulated CSI parameter digits with no length cap, and `_param()`/the SGR handler ran
bare `int()` on the result. A single crafted escape sequence with a long enough digit
run (e.g. from `cat`-ing an untrusted file, or a compromised remote program's output —
a classic terminal-emulator attack surface) would exceed Python's int-string-conversion
digit limit and raise an uncaught `ValueError`, crashing the whole parse loop on one
byte stream. Fixed by capping param length (7 digits) and param count (32) at
accumulation time, plus wrapping the `int()` calls in `try/except ValueError` as
defense in depth. Two regression tests added (`test_huge_csi_param_digit_run_does_not_
crash`, `test_huge_csi_param_count_does_not_crash`). **Lesson for future milestones**:
any place that accumulates attacker-controlled bytes into a buffer before parsing
(OSC content parsing, once that's implemented, is the next one that will need this)
needs an explicit cap from the start, not added after the fact.

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
- [x] CSI parameter accumulation hardened against the differential-DoS class
      (unbounded digit/param-count growth -> uncaught `ValueError`) — 2026-08-13
- [x] Alternate screen buffer (DECSET 47/1047/1049) + DECSC/DECRC (ESC 7/8) +
      IND/RI (ESC D/M) with scroll-region-aware linefeed — 2026-08-13, unit-tested
- [x] DECSTBM (`CSI r`) — narrows `scroll_top`/`scroll_bottom`, rejects invalid
      bounds (top>=bottom, out-of-range), resets cursor to home on set — 2026-08-15
- [x] Insert/delete line/char (IL/DL/ICH/DCH — `CSI L/M/@/P`), region-aware for
      line ops, clamped against huge counts — 2026-08-15, unit-tested
- [x] Live-test `python -m puppy` in a real terminal window — spawned 2026-08-15,
      user confirmed it looked fine. Not independently screenshotted/verified in
      detail (no way to see a live GUI window from here) — if anything subtle looks
      wrong later (colors, cursor drift, wrapping), this is the first place to doubt.
- [x] 256-color and 24-bit truecolor SGR (`38/48;5;n`, `38/48;2;r;g;b`, both `;`-
      and `:`-separated forms, including the `38:2:<colorspace-id>:r:g:b` variant) —
      2026-08-15, unit-tested; two real parsing gaps found and fixed (see the
      kitty-source verification pass above)
- [x] Scrollback — `deque(maxlen=2000)`, only real top-of-main-screen scrolls
      captured (not alt-screen, not narrowed-DECSTBM regions, not DL), `CSI 3 J`
      clears it — 2026-08-15, unit-tested
- [x] Kitty-source verification pass — DECSTBM clamping, IL/DL carriage-return,
      alt-screen 47/1047-vs-1049 cursor handling — 2026-08-15, see above, unit-tested
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

1. Bracketed paste (2004) / focus reporting (1004) / synchronized output (2026) —
   mode set/reset tracking only for now, these don't change rendering yet, just need
   the DECSET/DECRST modes recognized (extend `Parser._dispatch_private_mode`'s
   pattern, same as alt-screen). Synchronized output specifically should end up
   batching a burst of writes into one `Screen` update rather than applying byte by
   byte, once there's a renderer to actually batch updates for.
2. Then mouse protocols (1000/1002/1003/1006 SGR) and the OSC family (title,
   palette, clipboard, hyperlinks) — OSC content is currently fully discarded by
   the parser (`_finish_osc`), so this needs real buffering with a length cap from
   the start (see the security-fix lesson in Current status/2026-08-13).
3. Terminfo entry next, so real programs (vim, ncurses apps) detect what puppy
   actually supports instead of guessing from `$TERM`.
4. Then **decide the rendering/windowing toolkit** (pywayland/GTK4/SDL2, still
   deferred) — the text-only model is getting substantial enough that rendering is
   close to being the next real thing blocking progress, not scrollback/protocol
   work anymore.
4. Revisit the windowing-toolkit decision once the text-only model feels solid enough
   that rendering is the next real thing blocking progress.

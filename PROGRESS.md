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
5. **Back-color-erase (bce) was entirely missing** (found 2026-08-15, while researching
   the terminfo entry — `bce` is a real terminfo boolean capability, which is what
   prompted checking whether it was actually implemented): erase/insert/delete-char
   operations should fill newly-blank cells with the cursor's *current* SGR state, not
   a hard default. Confirmed against kitty's real `line_apply_cursor`/
   `linebuf_clear_lines` (`line.c`/`line-buf.c`): `ED`/`EL`/`ICH`/`DCH` all apply full
   current SGR (fg/bg/bold/underline/reverse — `cursor_as_gpu_cell` copies everything,
   not just background despite the capability's name) to the cells they blank, but
   `IL`/`DL` (`linebuf_insert_lines`/`linebuf_delete_lines`, via `clear_line_`) and a
   full alt-screen-entry/resize blank (`linebuf_clear`) do **not** — a real, confirmed
   asymmetry, not an inconsistency to "fix into" uniformity. `Screen` previously used a
   hard-default `Cell()` everywhere; now a `_bce_cell()` helper (current SGR, no
   hyperlink — confirmed `cursor_to_attrs` never carries hyperlink state) backs
   `_erase_line_from`, `erase_in_display` modes 2/3, `insert_chars`, and
   `delete_chars`, while `_shift_up`/`_shift_down` (backing `IL`/`DL` and top-of-screen
   scroll) and `_blank_grid` (alt-screen entry, resize) correctly keep plain `Cell()`.

**Lesson**: spec-knowledge implementations of "obscure-looking" behavior (exact
clamping rules, whether an operation also does a CR, which private modes share state,
which operations apply the current background to what they erase) should be
spot-checked against the real source when it's sitting right there in
`~/Projects/kitty`, not trusted from memory — none of these five were something a
plain reading of ECMA-48/xterm docs would have caught, and #5 specifically was only
found because writing an honest terminfo entry (which has a `bce` capability flag)
forced the question "do we actually do this?" Worth treating protocol/terminfo work
as a trigger for another verification pass, not just a one-time audit.

## Current status (2026-08-20)

**puppy is a real, runnable, typeable-into program with kitty keyboard protocol support
(including query-response), real bold/underline rendering, a general PTY write-back
channel, and a kitty graphics protocol model layer WITH real GPU rendering of placed
images — and it's now launchable live, from wofi or any app launcher, not just
`python -m puppy.render.app`.**

- `python -m puppy.render.app` (or just typing **`puppy`** — see the Launching section
  below) opens a live GLFW/wgpu window, spawns a real shell in a real PTY, renders the
  full grid every frame (bold via FreeType's real synthetic emboldening, underline at
  real font metrics, both proven with exact-pixel GPU readback), and accepts real
  keyboard/mouse input, including the full kitty keyboard protocol round-trip: `CSI =
  flags ; mode u` sets progressive-enhancement flags, `CSI ? u` gets a real response
  (`Screen.write_back`, wired to `session.write`).
- `Parser` has a fifth state (APC, `ESC _ ... ESC \`) feeding `src/puppy/graphics.py`'s
  `GraphicsManager` (`a=T`/`a=t` transmit, direct RGB/RGBA only, chunked reassembly
  included) via `Screen.graphics_command`.
- **New this session**: `src/puppy/render/graphics_renderer.py`'s `GraphicsRenderer`
  actually draws what `GraphicsManager` accumulates — a real textured-quad GPU pass,
  wired into `app.py`'s `draw_frame()` right after `CellRenderer.render()` so images
  composite on top of the cell grid. 5 new exact-pixel GPU-readback tests (placement
  positioning, RGB opacity, RGBA alpha-blending over existing content, multi-cell
  auto-sizing, empty-placements no-op) — see the Milestones entry below for the full
  kitty-source-verified design (`gpu_data_for_image`/`grman_update_layers` NDC-rect
  math, premultiplied-alpha blend, sRGB texture-format handling).
- **Also new this session**: `~/.local/bin/puppy` (venv-activating wrapper, standard
  RengeOS wrapper-script pattern — see `xtmux`/`arcticfox` for precedent) and
  `~/.local/share/applications/puppy.desktop` — puppy now shows up in wofi/any
  `.desktop`-aware launcher as **"puppy"**, `Categories=TerminalEmulator;`.
  `update-desktop-database` was run once already. **Not yet live-tried by the user** —
  next session, or whenever convenient: launch it via wofi (not `Mod+Alt+Space`'s
  RengeOS main-menu.py, which is a separate hand-built menu tree that would need its
  own edit to add an entry — this is the OS-level app-launcher / `wofi --show drun`
  path) and confirm it appears and opens correctly from there, not just via direct
  terminal invocation of the wrapper (already smoke-tested, see below).

240 tests passing (up from 110 at the very start of the rendering push, 235 at the
start of this session — the 5 new ones are `test_render_graphics_renderer.py`; the
.desktop/wrapper work added no tests, it's not Python code). `source
.venv/bin/activate` for any code work — see the Python-environment architecture
decision entry.

**Verified this session**: full test suite green (240 passed); `timeout 3 python -m
puppy.render.app` and `timeout 3 ~/.local/bin/puppy` both start and run for the full
3 seconds with no crash/traceback (only the pre-existing, harmless
`libdecor-gtk-WARNING` seen in every prior session's smoke test). **Not verified**: no
one has yet watched an actual image render in the live window (the GPU-readback tests
prove the pixel math is exactly correct in isolation; no real program was run inside
puppy that actually emits a kitty graphics APC sequence during a live session) — icat
or a hand-crafted printf of a real `\x1b_Ga=T,f=24,s=...,v=...;<base64>\x1b\\` sequence
against the live wofi-launched window would be the natural next confirmation, low
priority relative to real remaining gaps.

**Known gaps, all deliberate and documented** (not oversights): kitty keyboard protocol
has no alternate-key/text-embedding subfields, no hyper/meta modifiers, no push/pop
flags stack (single flat value); legacy encoding has no Alt/Meta-prefixed sequences, no
Ctrl+non-letter combos, no Shift+function-key variants; no strikethrough or kitty's
HSLuv-based automatic contrast override; kitty graphics has no PNG/compression/delete/
query/animation/unicode-placeholder/file-transmission support; graphics rendering has
no z-index layering (images always draw on top, in placement order), no cropping
(`src_rect` is always the full image), no scrollback-scroll tracking for images, and
one uniform-buffer+bind-group allocation per placement per frame (no batching —
fine for real placement counts, revisit only if profiling shows otherwise).

Full history of everything before today lives in the dated Milestones checklist below —
not duplicated here, per this file's own "replace, don't append" rule.

## Launching puppy

Three ways, all equivalent (the wrapper just activates the venv and runs the module):
- From a terminal: `puppy` (the `~/.local/bin/puppy` wrapper, on `$PATH`).
- From a terminal, explicit: `cd ~/Projects/puppy && source .venv/bin/activate &&
  python -m puppy.render.app`.
- From wofi / any `.desktop`-aware launcher: **puppy** (`TerminalEmulator` category).
  Not wired into RengeOS's own hand-built `main-menu.py` (`Mod+Alt+Space`) — that's a
  separate, deliberately curated menu tree; add an entry there too later if it should
  show up in both places, not done as part of this pass.

## Architecture decisions log

- **A plain top-level WGSL uniform struct only needs its largest-member alignment
  (8 bytes for `vec2<f32>`), not a stricter 16-byte rule.** Verified empirically before
  trusting it, not reasoned from memory of WGSL's spec: a throwaway shader with a
  40-byte (10-`f32`) uniform struct (needed to add `underline_y`/`underline_thickness`
  to `CellRenderer`'s existing `Uniforms`) read back every field's exact value
  correctly. The stricter 16-byte-multiple rule that's easy to half-remember from WGSL
  docs applies to storage-buffer *array element* stride (confirmed separately: the
  `Instance` struct's `flags: vec4<f32>` field, added for the underline flag, keeps
  the array stride a clean 16-byte multiple on purpose), not a single bound uniform
  struct.
- **`Screen` has a general PTY write-back channel — `write_back: Callable[[bytes],
  None]`, constructor-injected, defaulting to a no-op.** Surfaced 2026-08-16 while
  implementing the kitty keyboard protocol (`CSI ? u`, a program *querying* puppy's
  progressive-enhancement flags, needs a response written back to the child — confirmed
  via kitty's real `screen_report_key_encoding_flags`/`write_escape_code_to_child`) and
  built as a general mechanism immediately rather than a one-off, since DA1/DA2
  device-attribute queries, DSR status reports, and XTGETTCAP will all need the same
  thing later. `puppy.render.app` and `__main__.py` both wire it to `session.write`; the
  no-op default means every existing `Screen()`-with-no-PTY test/use keeps working
  unchanged. `report_key_encoding_flags` (`CSI ? u`'s handler) is the first real
  consumer.
- **Input bypasses `rendercanvas`'s own event abstraction; raw GLFW callbacks are
  registered directly on the underlying window handle instead.** Checked
  `rendercanvas`'s real installed source (`rendercanvas/glfw.py`) before building input
  handling, not assumed: its own `_on_key` handler silently discards every
  `GLFW_REPEAT` event (`else: # glfw.REPEAT / return`) as part of normalizing input
  into a cross-toolkit event schema — exactly the press/repeat/release granularity
  puppy chose GLFW specifically to get (the whole point, for the future kitty keyboard
  protocol). `Window`'s `set_key_handler`/`set_char_handler`/`set_mouse_button_handler`/
  `set_cursor_pos_handler`/`set_scroll_handler` call `glfw.set_*_callback` directly on
  `canvas._window` (a private rendercanvas attribute — deliberate, no public accessor
  exists, confirmed via introspection). Verified safe: `rendercanvas` registers
  *separate* callbacks for resize/close/focus/iconify that this doesn't touch, and the
  6 input callbacks being replaced are only used internally for the abstraction being
  deliberately bypassed, not anything render-loop-critical.
- **`Cell.fg`/`Cell.bg` int values are always a 0-255 palette index, normalized at SGR-
  parse time — never a raw SGR attribute code.** Real bug, found and fixed 2026-08-16
  while building the renderer's color resolution (`puppy.render.palette`), which needed
  an unambiguous meaning for these ints. `Screen.sgr()` used to store basic-color SGR
  codes (30-37/90-97) as-is; since `38;5;n` (256-color) also stores a raw 0-255 index in
  the *same field*, values 30-37/90-97 were ambiguous between "basic SGR color" and "a
  different, specific 256-color palette entry." Confirmed and fixed against kitty's real
  `cursor_from_sgr` (`cursor.c`): `case 30...37: fg = (attr-30)<<8|1` — same tag as the
  256-color path, i.e. kitty normalizes basic colors into indices 0-15 of the *same*
  unified space, never stores the raw code. `Screen.sgr()` now does the same
  (`p - 30` / `p - 90 + 8` for fg, `p - 40` / `p - 100 + 8` for bg). Affected 5 existing
  test assertions (fixed to expect the normalized index, e.g. `\e[41m` -> `bg == 1`, not
  `41`) — a real behavior change, not just an internal refactor, so anything reading
  `Cell.fg`/`bg` directly (only the renderer does, so far) must treat it as this
  unified 0-255 index space.
- **Rendering/windowing toolkit: DECIDED 2026-08-16 — `glfw` + `wgpu-py`/`rendercanvas`
  + `uharfbuzz` + `freetype-py`.** Long back-and-forth, worth recording in full since it
  won't be re-litigated without a real reason:
  1. Research doc's original three candidates (`pywayland`, GTK4, SDL2) were a Session
     85 placeholder list, not a rigorous comparison — re-examined from scratch.
  2. Checked what real terminals actually use, from their own source, not from memory:
     kitty = GLFW + OpenGL (confirmed in `~/Projects/kitty/kitty/glfw.c`/`glfw-wrapper.h`,
     both Wayland and X11 GLFW backends ship and are what's installed on this system).
     Ghostty (a terminal with very high kitty-protocol fidelity, second only to kitty
     itself) = GTK4 on Linux, confirmed via its own docs/wiki. foot = raw Wayland + fcft,
     but has materially weaker kitty-protocol support (no graphics protocol, partial
     keyboard protocol) than kitty/Ghostty — evidence that "less abstraction" doesn't
     reliably correlate with "more protocol fidelity."
  3. GTK4 was briefly the leading candidate (rich key-event API via
     `GtkEventControllerKey`, Pango for text shaping "for free", desktop-native fit) —
     but the user pushed back on defaulting to it just because it's the most
     batteries-included option, and asked what OdyTTY (the other terminal actually
     installed and daily-driven on this system) uses. Checked OdyTTY's real
     `Cargo.toml` on GitHub directly (not its marketing page, which 403'd): `winit`
     (windowing) + `wgpu` (GPU API, the same wgpu also used by WezTerm) + `swash`/
     `skrifa` (font shaping) + `tiny-skia` (rasterization) + `rustix` (PTY, same
     approach puppy already uses). Two real, locally-relevant reference terminals
     (kitty, OdyTTY) independently converged on "thin windowing crate + direct modern
     GPU API + real shaping libraries," not a full toolkit — GTK4/Ghostty is the
     outlier, not the pattern, among the terminals actually running on this machine.
  4. Found the direct Python equivalents of OdyTTY's exact stack, verified real and
     maintained (not just plausible names): `glfw` (ctypes bindings to the actual GLFW
     C library kitty uses — same proven Wayland backend), `wgpu-py`/`rendercanvas`
     (wraps `wgpu-native`, the literal same Rust `wgpu` crate OdyTTY links against,
     compiled to a C-ABI library; `rendercanvas` has first-class built-in GLFW canvas
     integration), `uharfbuzz` (real Cython bindings to actual HarfBuzz under the
     official HarfBuzz GitHub org, actively maintained — the same shaping engine kitty
     itself uses in C), `freetype-py` (real FreeType bindings, used as a direct
     dependency by fontTools). Rejected `pyglet` as a windowing candidate specifically
     because its Wayland support is a long-standing (since 2022) unresolved gap.
  5. **Verified the full pipeline actually works in this environment before
     committing**, not just that the packages import: a real GLFW window creates
     natively on Wayland (platform code confirms `GLFW_PLATFORM_WAYLAND`, not an
     XWayland fallback); a real `wgpu` adapter/device/surface pipeline configures
     against it (`Intel(R) Graphics (ADL GT2) via Vulkan`, the real iGPU); a round-trip
     clear+pixel-readback test against the real GPU (not a mock) proved exact-byte
     color accuracy across all 256 values; and real HarfBuzz shaping + FreeType
     rasterization against the actual system default font (DejaVu Markup Nerd Font)
     produced real glyph IDs, advances, and a rasterized bitmap.
  6. Kitty theme (`kitten themes`) compatibility was raised as a possible discriminator
     but turned out not to be one: kitty themes are just plain-text color config
     (`color0`-`color15`, `foreground`, `background`, etc.), completely decoupled from
     the rendering/windowing layer — any of the candidates could parse and apply them
     equally, so this didn't favor any option.
  - This matters most for the kitty keyboard protocol (needs real press/repeat/release
    events) and the kitty graphics protocol (needs real GPU texture compositing) —
    both are now unblocked to start once the basic window+render loop is built out
    further (see Milestones/Next steps).
- **GPU colors are linear-space; theme/SGR colors are sRGB bytes — always convert.**
  Confirmed by round-tripping clear+pixel-readback against the real GPU (exhaustively,
  all 256 byte values, zero mismatches): `wgpu`'s surface format is sRGB-encoded (e.g.
  `bgra8unorm-srgb`), and it treats `clear_value`/vertex-color input as **linear**,
  gamma-*encoding* it automatically on write. A theme hex color or SGR RGB value is
  always specified as an sRGB byte (the normal display convention) — passing
  `byte/255` straight to the GPU double-encodes and renders too bright/washed out.
  `puppy.render.color.srgb_to_linear`/`srgb_color` does the correct decode; any new
  code that hands a color to `GpuContext` must go through it, never a raw `/255`.
- **Python environment: `.venv`, not system Python, from this point on.** The parser/
  screen/mouse layers had no native dependencies and were fine installed system-wide
  (`--break-system-packages`) in earlier sessions. The render layer's GPU/windowing
  deps (`glfw`, `wgpu`, `rendercanvas`, `uharfbuzz`, `freetype-py`) are native
  packages that shouldn't pollute system Python — `~/Projects/puppy/.venv` (gitignored)
  is now the standard way to work on this project. `tests/test_render_*.py` use
  `pytest.importorskip` so the suite still runs (with those specific tests skipped,
  not failed) under system Python that hasn't installed the render deps — but real
  render-layer work needs the venv activated.
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
- [x] Bracketed paste (2004), focus reporting (1004), synchronized output (2026) —
      2026-08-15, state tracking only (`Screen.private_modes`, generic for any DEC
      private mode) — none of these change behavior yet, sync output specifically
      needs a real renderer to batch updates for, see Current status
- [x] Mouse protocols (1000/1002/1003/1006 SGR) — **encoding half done**, event
      *source* still missing (see Current status): `src/puppy/mouse.py`,
      `encode_sgr_mouse_event` (pure bit-encoding, verified against kitty's real
      `encode_mouse_event_impl`/`encode_button` in `mouse.c`) + `generate_mouse_
      report` (mode-gated via `Screen.private_modes`: 1000=press/release,
      1002=+drag, 1003=+pure motion, 1006 required for SGR encoding at all) —
      2026-08-15, unit-tested against hand-derived byte sequences. Legacy X10/
      UTF8 encoding (mode 1006 not set) is a deliberate, documented gap.
- [x] OSC family: title (0/1/2), palette (4), default fg/bg (10/11), clipboard (52),
      hyperlinks (8) — 2026-08-15, unit-tested, includes DoS-safety tests (huge code
      number, huge payload) — see Current status for the buffering/cap design
- [x] Terminfo entry — `terminfo/puppy.terminfo`, `use=xterm-256color` +
      overrides only for genuine divergences (`smcup`/`rmcup` trimmed to the
      mode-1049-only sequences puppy implements, `Tc`/`RGB` added for
      truecolor). Compiles clean with `tic -x` (`scripts/install-terminfo.sh`,
      installs to `~/.terminfo`, no sudo), and a real ncurses program
      (`curses.setupterm()`) accepts `TERM=puppy` and reports `bce: 1`
      correctly — 2026-08-15. Writing this honestly (checking whether `bce`
      was really implemented before inheriting the claim from xterm-256color)
      is what surfaced the back-color-erase gap, now fixed separately (see
      the kitty-source verification section). **Not yet wired into
      `PtySession`/`__main__.py`** — the proxy still inherits whatever `TERM`
      the host shell already has, deliberately, so as not to change the
      already-live-tested pass-through behavior without a fresh live check;
      trying `TERM=puppy python -m puppy` is the next live-test candidate.
- [x] **Decide rendering/windowing toolkit** — `glfw` + `wgpu-py`/`rendercanvas` +
      `uharfbuzz` + `freetype-py`, 2026-08-16, see the Architecture decisions log
      entry for full reasoning. `.venv` set up with all four installed.
- [x] Minimal GPU pipeline proof-of-concept — `src/puppy/render/gpu.py`
      (`GpuContext`: canvas-agnostic adapter/device/surface setup + a single
      clear-color render pass), `src/puppy/render/color.py` (sRGB↔linear
      conversion, exhaustively round-trip-verified against the real GPU across
      all 256 byte values), `src/puppy/render/window.py` (a live GLFW window
      wrapping `GpuContext` — lifecycle only, no key/mouse callbacks wired yet).
      Real, non-mocked pytest coverage via `rendercanvas.offscreen` (headless,
      no visible window, real numeric pixel readback) — 2026-08-16, 8 new
      tests. `Window` itself was smoke-tested once, briefly, non-interactively
      (create+verify+close, no event loop) — real GLFW+wgpu window creation on
      the live Wayland session confirmed working, `bgra8unorm-srgb` surface
      format. Not yet a real terminal renderer — no font/glyph/cell-grid
      drawing yet, just "open a window, clear it to an exact color, prove it."
- [x] Font shaping/rasterization layer — `src/puppy/render/font.py`,
      `FontRenderer`: real HarfBuzz shaping + real FreeType rasterization,
      glyph-id caching. **Real finding while building this**: cell width/height
      must come from FreeType's *hinted* advance, not HarfBuzz's or FreeType's
      unhinted/design-unit value — confirmed empirically (DejaVu Markup Nerd
      Font, 16px: hinted=10.0px exactly, unhinted/design-unit=9.640625px,
      consistent across 'M'/'i'/'W'/'.', confirming the font genuinely is
      monospace and hinting is what snaps it to a clean integer pixel grid).
      HarfBuzz's x_advance is deliberately NOT used for cell positioning —
      cells sit at a fixed column*cell_width grid regardless, matching how a
      monospace terminal grid actually works. Also fixed a real pitch-vs-width
      bug before it shipped: FreeType's `bitmap.buffer` is `pitch*rows` bytes
      (row-padded), not `width*rows` — naively treating it as tightly packed
      would corrupt any glyph where pitch != width; fixed with a per-row copy,
      locked in with a regression test. 2026-08-16, 8 tests, real font
      (whatever fontconfig resolves for `monospace`, portable across
      machines, no hardcoded path), real rasterized pixel data asserted
      non-empty, not just "didn't crash."
- [x] Glyph-grid renderer proper — `src/puppy/render/atlas.py` (`GlyphAtlas`:
      packs rasterized glyphs into fixed cell-sized slots, baseline-positioned
      within each slot at blit time — design matches kitty's real sprite atlas,
      confirmed via `cell.slang`'s `to_sprite_pos`; handles zero-size/oversized
      glyphs without crashing or corrupting neighboring slots; tracks a dirty
      rect for incremental GPU re-upload) + `src/puppy/render/cell_renderer.py`
      (`CellRenderer`: one instanced-quad WGSL draw call renders every cell —
      storage-buffer instance data, uniform buffer for screen/cell/atlas
      geometry, texture+sampler binding, fg/bg composited via the
      premultiplied "over" blend confirmed from kitty's real
      `alpha-blend.slang`: `result = over + under*(1-over.a)`). Prototyped and
      verified the whole wgpu-py pipeline shape (storage buffers, bind group
      layouts, WGSL struct alignment) empirically before writing the real
      module. Checked kitty's *real* current shader source for the
      compositing formula (`~/Projects/kitty/kitty/shaders/cell.slang` +
      `alpha-blend.slang` — correcting a stale assumption: kitty moved from
      `.glsl` to `.slang` at some point, `cell_fragment.glsl` doesn't exist in
      the current clone). Kitty's real shader is far more sophisticated than
      this v1 — HSLuv-based automatic fg/bg contrast override, cursor/
      selection/underline/strikethrough composited via a texture *array*
      atlas, gamma-adjustment modes — all deliberately out of scope here, a
      documented later milestone, not this pass. 2026-08-16, 12 new tests
      (9 atlas, 3 cell-renderer), all real GPU + real pixel readback: a
      synthetic half-inked glyph produces *exact* fg pixels where alpha=1 and
      *exact* bg pixels where alpha=0; a real rasterized 'M' from the actual
      system font renders as neither a flat block nor a no-op; two cells in
      one draw call render independently. **Not yet wired to a live `Window`
      render loop or a real `Screen` instance** — proven technology, not yet
      "open puppy and see your shell," see Current status.
- [x] **Wired into an actual running program** — `src/puppy/render/app.py`
      (`run()`/`build_instances()`): `PtySession` -> `Parser` -> `Screen` ->
      per-cell `FontRenderer`/`GlyphAtlas` lookup -> `CellRenderer`, redrawing
      the full grid every frame in a live GLFW loop. `python -m
      puppy.render.app` opens a real window and runs a real shell —
      confirmed with one deliberate 3-second live run, no crash/traceback.
      Also added `puppy.render.palette` (ANSI 256-color table +
      OSC-4-override spec parsing) to resolve `Cell.fg`/`bg` into real RGB,
      which surfaced and fixed a real pre-existing bug in `Screen.sgr()`
      (basic SGR color codes were stored ambiguously with 256-color
      indices — see the Architecture decisions log entry). 2026-08-16, 22
      new tests (14 palette, 8 app-wiring). **Still no key/mouse input** —
      the window opens and the shell runs, but nothing you type reaches it
      yet; that's the next milestone. Bold/underline parse correctly into
      `Cell` but aren't visually rendered yet (no bold font variant or
      underline decoration sprite) — a deliberate, documented gap.
- [x] Key/mouse event capture wired to the live `Window` — real GLFW callbacks
      (bypassing `rendercanvas`'s own event abstraction, which drops key-repeat
      events, see Architecture decisions log) feed `src/puppy/render/
      input_state.py`'s `InputState`, which encodes via `src/puppy/keyboard.py`
      (new: legacy xterm/VT220-style key encoding — DECCKM-aware normal/
      application-mode arrows, Home/End/PageUp/PageDown/Insert/
      Delete/F1-F12/Enter/Backspace/Tab/Escape taken from puppy's own compiled
      terminfo entry, Ctrl+A-Z control bytes) and `puppy.mouse` (already built)
      and writes to `PtySession`. 2026-08-16, 22 new tests (10 keyboard, 12
      input-state via a stub session, no real PTY needed). Verified live: the
      wired-up app still starts and runs continuously with no crash. Documented
      v1 gaps: no Alt/Meta-prefixed sequences, no Ctrl+non-letter combos, no
      Shift+function-key variants.
- [x] Kitty keyboard protocol (CSI u) — **core encoding + flag-set done**, real
      remaining gaps documented below. `src/puppy/kitty_keyboard.py`
      (`encode_kitty_key_event`): format/modifier-bit-values/action-subfield/
      functional-key-PUA-codepoints all verified exact against kitty's real
      `key_encoding.c` + `glfw-wrapper.h`'s `GLFW_FKEY_*` enum, not assumed
      from the spec doc alone. `Screen.key_encoding_flags` +
      `set_key_encoding_flags(val, how)` (how=1 set/2 OR/3 AND-NOT, exact
      against kitty's real `screen_set_key_encoding_flags`), and `Parser` now
      recognizes `CSI = flags ; mode u` as a distinct prefix from `?`
      (confirmed distinct branches in kitty's real `vt-parser.c`).
      `InputState` routes through this encoder once flags are nonzero,
      suppressing the char callback to avoid double-sending (a real
      interaction risk caught before it shipped, not after). `CSI ? u`
      query-response now works too (see the PTY write-back milestone
      below) — a real program can round-trip set-flags/query-flags
      correctly, not just set them one-way. 2026-08-16, 27 new tests (11
      kitty_keyboard, 5 Screen, 3 Parser, 4 InputState mode-switching +
      others already counted), plus 5 more for the write-back round-trip
      itself (see below). Verified live: still starts and runs with no
      crash. **Real, documented gaps**: no alternate-key/text-embedding
      subfields, no hyper/meta modifiers (stock GLFW limitation), plain-key
      codepoints assume US/QWERTY-like layout, no `CSI > u`/`CSI < u`
      push/pop stack (single flat flags value only).
- [x] General PTY write-back channel — `Screen.write_back` (constructor-
      injected `Callable[[bytes], None]`, no-op default so every existing
      `Screen()`-with-no-PTY test/use keeps working unchanged), wired to
      `session.write` in both `puppy.render.app` and `__main__.py`.
      `report_key_encoding_flags` (`CSI ? u`'s handler) is the first real
      consumer — response format confirmed against kitty's real
      `screen_report_key_encoding_flags`. 2026-08-16, 6 new tests (3
      `Screen`-level with a stub write_back list, 3 `Parser`-level
      end-to-end through real escape bytes). Built as a general mechanism
      on purpose, not a one-off — DA1/DA2 device-attribute queries and DSR
      status reports will reuse the same channel later.
- [x] Bold/underline visual rendering — bold via FreeType's real synthetic
      emboldening (`FT_GlyphSlot_Embolden`, an outline-based thickening
      applied before rasterization — verified to add ~41% more covered
      pixels on a test glyph, not a no-op; the standard real fallback
      technique terminal emulators use without a dedicated bold font file,
      a real bold face would look better but isn't wired up, documented
      gap). `FontRenderer.rasterize`/`rasterize_char` now cache by
      `(glyph_id, bold)`, and `GlyphAtlas` keys by whatever the caller
      passes (a `(glyph_id, bold)` tuple from `build_instances`), so bold
      and regular variants get separate atlas slots. Underline drawn at the
      font's real `underline_position`/`underline_thickness` metrics
      (confirmed these are in *font design units*, not size-scaled pixels,
      by reading freetype-py's actual property source before trusting the
      magnitude — a real, easy-to-get-wrong subtlety) via a new `flags`
      field on the GPU instance struct and an underline band drawn directly
      in the fragment shader (no separate draw call/sprite). 2026-08-16, 11
      new tests: `FontRenderer` bold-produces-more-ink + separate-caching
      (pure Python), 2 real-GPU pixel-exact underline tests (band present
      at the right row when flagged, absent when not — exact fg/bg colors,
      not approximations), 2 `build_instances` wiring tests (bold gets a
      different atlas slot, underline sets the flag). Verified live: still
      starts and runs with no crash. **Real, documented gaps**: no
      strikethrough, no kitty's HSLuv-based automatic fg/bg contrast
      override, no cursor/selection compositing.
- [x] Kitty graphics protocol v1 model layer (direct RGB/RGBA transmit+display,
      chunked reassembly) — `Parser` gained a 5th state, APC (`ESC _ ... ESC \`,
      ST-terminated only, no BEL form — mirrors the OSC buffering pattern with its
      own length cap, `_MAX_APC_LEN`); `_dispatch_apc` recognizes the `G` marker,
      splits comma-separated `key=value` control data from the base64 payload at
      the first `;`, and calls `sink.graphics_command(control, payload)`.
      `src/puppy/graphics.py` (`GraphicsManager`, `Image`, `Placement`,
      `_PendingLoad`): confirmed against kitty's real `graphics.c`
      (`grman_handle_command`'s action-dispatch switch, `handle_add_command`,
      `load_image_data`, the `INIT_CHUNKED_LOAD` macro) that action absent behaves
      like `a=t` (transmit-only, no placement — grouped with `case 0`/`case 't'`
      in kitty's real switch, distinct from `case 'T'`), and that a continuation
      chunk (`m=1` on all but the last) carries only `m` and payload — the
      in-progress load, not the image id, is what a continuation targets, so
      repeated/wrong control keys on a continuation are correctly ignored (real
      finding, tested: `test_chunked_transmission_continuation_ignores_repeated_control_keys`).
      Sizing/DoS handling matches kitty's own real behavior for this exact case:
      direct RGB/RGBA has an *exact* expected size from declared `width*height*bpp`
      (`MAX_IMAGE_DIMENSION` bounds the declared dimensions themselves, confirmed
      against kitty's real constant), so both an overlong chunk and a short/
      incomplete transmission are rejected rather than silently accepted, and the
      loading slot is always cleared afterward so a rejected/short load can't wedge
      a later well-formed command into being treated as its continuation. v1 scope
      deliberately excludes (each a real separate follow-up, not attempted here):
      `f=100` (PNG), compression (`o=z`), `a=p` (put/display-only on an existing
      image), `a=d` (delete), `a=q` (query), `a=a`/`a=f` (animation), `a=c`
      (compose), unicode-placeholder, file/shm transmission (`t=f`/`t=t`/`t=s`).
      2026-08-19, 17 new tests (14 `test_graphics.py` model-layer, 3
      `test_parser.py` end-to-end through real APC escape bytes including a
      truncation-cap test). **GPU rendering of placed images is a separate, later
      pass** — a real texture + draw path, distinct from the glyph atlas, same as
      always planned; images currently just accumulate in
      `Screen.graphics.images`/`.placements` with nothing consuming them yet.
- [x] Kitty graphics: GPU rendering of placed images (texture + draw path) —
      `src/puppy/render/graphics_renderer.py` (`GraphicsRenderer`), wired into
      `app.py`'s `draw_frame()` right after `CellRenderer.render()` (`load_op:
      load`, not `clear`, so it draws on top of the already-rendered cell grid
      in the same frame — a second render pass in a fresh command encoder,
      same submission model `CellRenderer.render` already uses). Confirmed
      against kitty's real `shaders/graphics.slang` + `graphics.c`
      (`gpu_data_for_image`, `grman_update_layers`): one textured quad per
      placement, `dest_rect` in NDC with y flipped top-to-bottom (top=+1,
      bottom=-1 — same convention `CellRenderer`'s own vertex shader already
      uses, confirmed consistent rather than assumed), `src_rect` always the
      full `[0,1]` identity rect (v1 has no cropping support, matches the
      model layer's own scope), and a premultiplied-alpha "over" blend —
      texture data is straight (non-premultiplied) alpha on the wire, so the
      fragment shader premultiplies before the GPU blend stage, ported
      directly from kitty's real `texture_is_not_premultiplied = true` path
      rather than assumed. Placement auto-sizing (`num_cols`/`num_rows` from
      `ceil(image.width/cell_width)`/`ceil(image.height/cell_height)`) matches
      kitty's real `update_dest_rect` auto-cols/auto-rows formula, since v1
      never parses explicit `c`/`r` control keys. **Real finding applied, not
      just inherited from the clear_value gotcha**: the uploaded texture uses
      `rgba8unorm_srgb` (not plain `rgba8unorm`) specifically so the GPU
      auto-decodes sRGB-encoded wire pixel bytes to linear on sample, mirroring
      how the surface format auto-encodes linear back to sRGB on write — same
      round-trip rule as `GpuContext`'s `clear_value`/vertex-color handling,
      applied to a *texture* for the first time rather than a per-vertex color.
      RGB (`f=24`, no alpha channel) images are padded to RGBA (alpha=255) in
      Python before upload, since WebGPU sampled-texture formats don't include
      a plain 3-channel option (a real, confirmed constraint, not an
      oversight). One uniform-buffer + bind-group allocation per placement per
      frame — no `group_count`-style batching like kitty's real
      implementation, a deliberate v1 simplification given realistic placement
      counts, same "don't pre-optimize" discipline as the parser. 2026-08-20,
      5 new tests (`test_render_graphics_renderer.py`), all real GPU + exact
      pixel-readback proofs: an RGB image renders pure fg color exactly where
      placed and leaves the rest of the frame at the untouched clear color; an
      RGBA image with alpha=0 leaves the existing background color completely
      unchanged (proves real alpha blending, not an opaque overwrite); a
      multi-cell image (8px wide over 4px cells) covers exactly
      `ceil(8/4)=2` cells and no more; placement position is driven by
      `row`/`col`, not always the origin; zero placements is a true no-op.
      Verified live: `timeout 3 python -m puppy.render.app` still starts and
      runs with no crash. **Not yet confirmed with a real live image** — no
      one has watched an actual kitty-graphics-emitting program (icat, a
      hand-crafted printf of a real APC sequence) render inside the live
      window; the exact-pixel GPU tests prove the render math in isolation,
      this is a separate, lower-priority live-confirmation step, see Current
      status.
- [ ] Kitty graphics: PNG format (`f=100`), compression (`o=z`)
- [ ] Kitty graphics: `a=p`/`a=d`/`a=q` (put/delete/query), animation (`a=a`/`a=f`)
- [ ] Kitty graphics: unicode-placeholder image-in-text method
- [ ] Kitty graphics: z-index layering, image cropping (`src_rect`), scrollback-scroll
      tracking, uniform/bind-group batching (all deliberate v1 simplifications, see
      the GPU-rendering Milestones entry above — none are oversights, revisit only
      if a real program's output needs them)
- [ ] Sixel graphics (fallback/parity with non-kitty terminals)
- [ ] Remaining kitty misc extensions (text-sizing, DnD, multi-cursor, file-transfer,
      notifications, pointer-shapes, color-stack, DECCARA)
- [x] `.desktop` file + wofi/launcher integration — `~/.local/bin/puppy` (wrapper,
      standard RengeOS pattern: `cd` into the project, activate `.venv`, `exec python
      -m puppy.render.app "$@"` — matches `xtmux`/`arcticfox`'s existing wrapper
      style) + `~/.local/share/applications/puppy.desktop`
      (`Categories=TerminalEmulator;`, `Name=puppy`). `update-desktop-database` run
      once. 2026-08-20. Verified: `timeout 3 ~/.local/bin/puppy` starts and runs with
      no crash, same as the direct-module invocation. **Not yet verified**: launching
      it live by actually finding/clicking it in wofi (or `wofi --show drun`) hasn't
      been tried by a human yet — only direct terminal invocation of the wrapper
      script itself was smoke-tested. Deliberately NOT added to RengeOS's own
      hand-built `main-menu.py` (`Mod+Alt+Space`) — that's a separate, curated menu
      tree; add it there too later if wanted, wasn't part of this pass.

## File map

```
puppy/
  PROGRESS.md          this file
  pyproject.toml        now declares glfw/wgpu/rendercanvas/uharfbuzz/freetype-py
  .venv/                 gitignored, real dependency install lives here — activate it
  src/puppy/
    __init__.py
    __main__.py         pass-through+dump harness (current entry point)
    pty_session.py       PtySession — spawn/read/write/resize a real PTY
    parser.py            Parser — byte-level VT/ANSI state machine
    screen.py             Screen, Cell — in-memory grid + cursor + SGR attrs
    mouse.py               SGR mouse-event encoding (puppy.mouse)
    keyboard.py             legacy GLFW-key -> byte-sequence encoding
    kitty_keyboard.py        CSI u progressive-enhancement key encoding
    graphics.py               GraphicsManager — kitty graphics protocol model layer (no rendering)
    render/
      __init__.py          toolkit-choice rationale pointer
      gpu.py                 GpuContext — canvas-agnostic adapter/device/surface + clear()
      color.py                sRGB<->linear conversion (GPU wants linear, themes are sRGB)
      window.py                live GLFW window + raw input-callback registration
      font.py                   FontRenderer — HarfBuzz shape + FreeType rasterize + cache
      atlas.py                   GlyphAtlas — packs glyphs into fixed cell-sized slots
      cell_renderer.py            CellRenderer — instanced-quad WGSL draw, fg/bg compositing
      palette.py                   ANSI 256-color table + OSC-4 spec parsing -> RGB
      input_state.py                InputState — GLFW callback data -> PTY bytes
      graphics_renderer.py           GraphicsRenderer — GPU textured-quad draw of placed images
      app.py                          run()/build_instances() — the actual wired-up program
  terminfo/
    puppy.terminfo       terminfo source, use=xterm-256color + real overrides
  scripts/
    install-terminfo.sh  tic -x install to ~/.terminfo, no sudo
  tests/
    test_parser.py
    test_screen.py
    test_mouse.py
    test_keyboard.py       legacy key-encoding correctness, no GPU/window needed
    test_kitty_keyboard.py  CSI u encoding correctness, no GPU/window needed
    test_graphics.py        GraphicsManager model-layer correctness, no GPU/window needed
    test_render_font.py   real shaping/rasterization, portable (fc-match, no hardcoded font)
    test_render_atlas.py   pure packing/blit logic, no GPU needed
    test_render_cell_renderer.py  real GPU + real pixel readback, exact-color proofs
    test_render_color.py  pure sRGB/linear math, no GPU needed
    test_render_gpu.py     real wgpu + offscreen canvas, real pixel readback, skips if no adapter
    test_render_palette.py  ANSI-256/OSC-4-spec-parsing correctness, no GPU needed
    test_render_app.py      build_instances() correctness, no GPU needed (font/atlas only)
    test_render_input_state.py  InputState correctness via a stub session, no real PTY
    test_render_graphics_renderer.py  real GPU + real pixel readback, exact-color proofs

~/.local/bin/puppy                  wrapper: cd + activate .venv + exec python -m puppy.render.app
~/.local/share/applications/puppy.desktop   wofi/launcher entry, Categories=TerminalEmulator
```

## Next steps (pick up here)

**Before anything else: `source .venv/bin/activate`** — the render layer's
dependencies live there, not in system Python. `pip install -e .` again if the venv
is ever recreated (it's gitignored).

Everything through "GPU rendering of placed images" and "`.desktop` file + wofi
integration" is now DONE (2026-08-20) — see the two `[x]` Milestones entries above for
full detail on each. **Nothing is mid-flight; there is no unfinished code to pick back
up.** The items below are genuinely the next things to pick, not a continuation of
something half-built.

1. **Two small live-confirmation steps, low effort, worth doing first since they're
   cheap and this session couldn't do them (no way to click a live GUI or watch a
   window from here):**
   - Launch **puppy** from wofi (or `wofi --show drun`) and confirm it appears and
     opens correctly — only direct terminal invocation of `~/.local/bin/puppy` has
     been smoke-tested (starts, runs 3s, no crash), the actual wofi-launcher path
     itself hasn't been clicked by a human yet.
   - Get a real image to render inside the live window — e.g. `printf` a hand-built
     `\x1b_Ga=T,f=24,s=<w>,v=<h>,i=1;<base64 RGB bytes>\x1b\\` into a shell running
     inside puppy, or use `kitten icat` / `chafa --format=kitty` if installed. The 5
     GPU-readback tests prove the render math is exactly correct in isolation, but no
     one has watched it happen live yet.
2. **Kitty graphics: next real protocol milestone — pick one.** All deliberately out
   of v1 scope so far, each independent, see the `[ ]` Milestones entries above for
   the exact key/action list each needs:
   - `f=100` (PNG) + `o=z` (compression) — the two together, since real-world tools
     (icat, chafa, etc.) send PNG far more often than raw RGB/RGBA. Needs a PNG
     decoder (check what's already a dependency — Pillow isn't currently one, would
     be a new addition — vs. writing a minimal PNG-chunk parser by hand) and zlib
     (stdlib `zlib.decompress`, no new dependency) for `o=z`.
   - `a=p`/`a=d`/`a=q` (put an already-transmitted image at a new position / delete /
     query without displaying) — smaller, self-contained additions to
     `GraphicsManager.handle_command`'s action dispatch.
   - z-index layering — currently images always draw on top of everything in
     placement order; real programs (e.g. anything drawing a background image) rely
     on negative z-index drawing *below* the cell grid, which needs `GraphicsRenderer`
     to interleave with (or run before) `CellRenderer` rather than always running
     after it — a real architectural change to `app.py`'s `draw_frame()`, not just a
     `GraphicsManager` addition. Check kitty's real z-index tiers (below/negative/
     positive, confirmed present in `grman_update_layers`, not yet re-read in detail)
     before designing this one.
3. Separately, whenever there's a spare cycle, unrelated to graphics: live-test
   `TERM=puppy python -m puppy` (the *text* pass-through harness, `__main__.py`) in a
   real terminal window (a real ncurses/vim session using the terminfo entry, not just
   `curses.setupterm()` accepting it headlessly) — still pending since it was first
   noted, needs an interactive session.

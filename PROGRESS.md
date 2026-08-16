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

## Current status (2026-08-16)

**Rendering works.** The full pipeline from a rasterized glyph to an actual composited
pixel on screen is built and verified against the real GPU, not mocked: `GpuContext`
(canvas-agnostic adapter/device/surface setup), `FontRenderer` (real HarfBuzz shaping +
FreeType rasterization, glyph-id caching), `GlyphAtlas` (packs rasterized glyphs into a
fixed-cell-size sprite grid — design matches kitty's real sprite atlas, confirmed via its
`cell.slang`), and `CellRenderer` (an instanced-quad WGSL draw: one draw call renders
every cell, sampling its glyph from the atlas and compositing fg-over-bg with the
premultiplied "over" blend confirmed from kitty's real `alpha-blend.slang`). Proven with
real pixel-exact readback tests, not approximations: a synthetic half-inked glyph produces
*exact* fg-color pixels where alpha=1 and *exact* bg-color pixels where alpha=0, and a
real rasterized 'M' from the system's actual default font renders as neither a flat block
nor a no-op — genuine glyph shape survives the whole pipeline. Two independent cells in
one draw call render independently (proven, not assumed). 139 tests passing (up from 110
at the start of this pass), all render-layer tests gracefully skip under a Python
environment without the render deps — **`source .venv/bin/activate` for real render work
from now on**, see the Python-environment architecture decision entry.

**Not yet done**: none of this is wired to a live `Window`'s render loop or to an actual
`Screen` instance yet — what exists is the proven rendering *technology*, tested via
`rendercanvas.offscreen` with hand-built instance data, not "open `puppy` and see your
shell." The next step is exactly that wiring — walk `Screen.grid`, shape+atlas-pack each
cell's `Cell.char`, build the instance array, call `CellRenderer.render` in a live GLFW
event loop each frame.

Full history of everything before today lives in the dated Milestones checklist below —
not duplicated here, per this file's own "replace, don't append" rule.

## Architecture decisions log

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
- [ ] Key/mouse event capture wired to the live `Window` (GLFW callbacks ->
      `puppy.mouse.generate_mouse_report` for mouse, and the eventual kitty
      keyboard protocol encoder for keys) — `PtySession.write` is the
      already-existing sink, just needs a real source now that one exists.
- [ ] Kitty keyboard protocol (CSI u) — encoding logic can now be built
      against real GLFW key/scancode/modifier events (`glfw.set_key_callback`
      gives press/repeat/release natively), following the same
      "encode_*_event, pure function, unit-tested against known bytes" pattern
      already used for mouse in `src/puppy/mouse.py`.
- [ ] Kitty graphics protocol (RGB/RGBA/PNG direct mode first)
- [ ] Kitty graphics: unicode-placeholder image-in-text method
- [ ] Sixel graphics (fallback/parity with non-kitty terminals)
- [ ] Remaining kitty misc extensions (text-sizing, DnD, multi-cursor, file-transfer,
      notifications, pointer-shapes, color-stack, DECCARA)

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
    mouse.py               SGR mouse-event encoding, no event source wired up yet
    render/
      __init__.py          toolkit-choice rationale pointer
      gpu.py                 GpuContext — canvas-agnostic adapter/device/surface + clear()
      color.py                sRGB<->linear conversion (GPU wants linear, themes are sRGB)
      window.py                live GLFW window wrapping GpuContext, lifecycle only
      font.py                   FontRenderer — HarfBuzz shape + FreeType rasterize + cache
      atlas.py                   GlyphAtlas — packs glyphs into fixed cell-sized slots
      cell_renderer.py            CellRenderer — instanced-quad WGSL draw, fg/bg compositing
  terminfo/
    puppy.terminfo       terminfo source, use=xterm-256color + real overrides
  scripts/
    install-terminfo.sh  tic -x install to ~/.terminfo, no sudo
  tests/
    test_parser.py
    test_screen.py
    test_mouse.py
    test_render_font.py   real shaping/rasterization, portable (fc-match, no hardcoded font)
    test_render_atlas.py   pure packing/blit logic, no GPU needed
    test_render_cell_renderer.py  real GPU + real pixel readback, exact-color proofs
    test_render_color.py  pure sRGB/linear math, no GPU needed
    test_render_gpu.py     real wgpu + offscreen canvas, real pixel readback, skips if no adapter
```

## Next steps (pick up here)

**Before anything else: `source .venv/bin/activate`** — the render layer's
dependencies live there, not in system Python. `pip install -e .` again if the venv
is ever recreated (it's gitignored).

1. **Wire it all together: `Screen` -> live `Window` render loop.** Everything
   needed exists and is tested in isolation (`PtySession`, `Parser`, `Screen`,
   `FontRenderer`, `GlyphAtlas`, `CellRenderer`) but nothing has been connected into
   an actual running program yet. Concretely: a loop that (a) reads `PtySession`
   output, feeds it through `Parser` into `Screen` (already exactly what
   `__main__.py`'s pass-through harness does), (b) each frame, walks `Screen.grid`,
   for each `Cell` calls `font.glyph_id_for_char`/`atlas.get_or_add` and builds the
   `INSTANCE_DTYPE` array (colors through `srgb_color`, per the linear-vs-sRGB
   decision), (c) calls `CellRenderer.render(instances)`, (d) polls the `Window` for
   close/resize. This is the actual "run puppy and see your shell render" milestone
   — everything before it was proving the pieces work, this is making them a program.
2. Wire real key/mouse events into the live `Window`: GLFW's `set_key_callback`
   (press/repeat/release natively — exactly what the kitty keyboard protocol needs)
   and `set_mouse_button_callback`/`set_cursor_pos_callback`/`set_scroll_callback` for
   mouse, feeding `puppy.mouse.generate_mouse_report` (already built) and
   `PtySession.write` (already built) — this is mostly plumbing two already-tested
   halves together, not new protocol logic.
3. Then the kitty keyboard protocol itself (CSI u encoding) — same pattern as
   `mouse.py`: a pure `encode_*_event` function unit-tested against known byte
   sequences, verified against kitty's real `keys.c`/`key_encoding.c` before trusting
   the encoding (same verification discipline used throughout this project).
4. Separately, whenever there's a spare cycle: live-test `TERM=puppy python -m
   puppy` in a real terminal window (a real ncurses/vim session using the terminfo
   entry, not just `curses.setupterm()` accepting it headlessly) — still pending,
   needs an interactive session, not blocking the render work above.

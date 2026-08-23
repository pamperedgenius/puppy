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

## Current status (2026-08-24)

**Second live user bug report this session, all 3 real issues fixed, 273 tests
passing (up from 259).** User compared puppy directly against xfce4-terminal/OdyTTY
and flagged: cat-art body too solid-black, no blur (unlike OdyTTY), a fish "Primary
Device Attribute" 10s-timeout warning, and launch still feeling slow.
- **DA1/DA2 device attribute queries** (real bug, not cosmetic): puppy never answered
  `CSI c`/`CSI > c` at all, so fish/tish blocked ~10s on shell startup and printed a
  degraded-mode warning. `Screen.report_primary/secondary_device_attributes()` +
  `Parser` now track a `>` prefix (new, parallel to the existing `?`/`=` tracking) and
  respond `CSI ?62;c` / `CSI >1;0;1c` via the existing write_back channel (confirmed
  format against kitty's real `report_device_attributes`/`da1`/`da2`). Live-confirmed:
  a 15s run now reaches a clean prompt with no warning, past the old 10s cutoff.
- **Real bold font face**, replacing synthetic-embolden-only: `FontRenderer` now loads
  a genuine bold `.ttf` (resolved via `fc-match "monospace:bold"`, same fontconfig
  mechanism as the regular face) when the system has a distinct one, falling back to
  the old `FT_GlyphSlot_Embolden` path otherwise (still real, still tested). Verified
  before trusting: regular/bold DejaVu Markup Nerd Font faces report identical hinted
  cell metrics and matching cmap indices for the punctuation set unifetch's art uses.
  **Honest caveat**: a rigorous controlled A/B (identical byte stream, real GPU
  readback, only the font config changed) showed this closes only a small part
  (~2% fewer pure-black pixels) of the visual gap against xfce4-terminal — an initial
  cross-terminal screenshot comparison suggesting a much bigger (~5x) gap turned out to
  be confounded by different window/cell sizes between the two captures, not a clean
  signal. The bulk of "doesn't fit well against the background" is the *content*: the
  active theme's own `unifetch-colors.conf` hardcodes literal `#000000` (real 24-bit
  truecolor, `\x1b[38;2;0;0;0m`) for part of the cat outline, which is genuinely
  near-invisible against this theme's `#000029` background in *any* spec-compliant
  truecolor terminal, puppy included — not something to "fix" by deviating from the
  color a program actually requested. A real fix belongs in the theme's own
  `unifetch-colors.conf` (RengeOS/theme-switcher scope, not this repo), not here.
- **Blur/transparency now targetable**: puppy's window had no Wayland app-id at all
  (`niri msg windows` showed `app_id: null`), so RengeOS's existing per-window
  blur/opacity system (`window-effect-toggle.py`, app-id-keyed niri window-rules,
  `Mod+Ctrl+A`) had nothing to match against — this was never a puppy rendering gap;
  niri applies blur as a compositor-level effect on the whole surface regardless of
  what the app itself renders. Fixed in `Window.__init__` (`window.py`) via
  `glfw.window_hint_string(glfw.WAYLAND_APP_ID, "puppy")` — non-obvious gotcha found
  and worked around: `rendercanvas.glfw.GlfwRenderCanvas.__init__` calls `glfw.init()`
  as its first step, which resets window hints on a real first init, silently wiping
  out a hint set before constructing `RenderCanvas`; calling `glfw.init()` ourselves
  first (real GLFW semantics: a second `glfwInit()` while already initialized is a
  no-op, confirmed against GLFW's own docs, not assumed) makes hints set right after
  survive into RenderCanvas's own (now second, no-op) `glfw.init()` call. Live-
  confirmed: `niri msg windows` now reports `app_id: "puppy"`.
- **Launch speed**: not re-investigated this pass — no new information beyond the
  2026-08-20 measurement (~1.1s, ~75% wgpu adapter/shader bring-up). Flagging honestly
  rather than guessing: the next real lever would be wgpu-native pipeline caching
  across process launches (unexplored, see the 2026-08-20 entry below) or restructuring
  GPU init order — both bigger, riskier changes not attempted reactively mid-session.

## Current status (2026-08-22)

**puppy is a real, runnable, typeable-into program with kitty keyboard protocol support
(including query-response), real bold/underline rendering, a general PTY write-back
channel, a kitty graphics protocol layer (direct RGB/RGBA **and now real PNG +
zlib-compressed transmission**) WITH real GPU rendering of placed images —
**confirmed live for the first time this session**, not just via GPU-readback unit
tests — and it's launchable from wofi/any `.desktop`-aware launcher, not just
`python -m puppy.render.app`. 259 tests passing (up from 248).

- `python -m puppy.render.app` (or just typing **`puppy`** — see Launching below) opens
  a live GLFW/wgpu window, spawns a real shell in a real PTY, renders the full grid
  every frame (bold via FreeType's real synthetic emboldening, underline at real font
  metrics, both proven with exact-pixel GPU readback), and accepts real keyboard/mouse
  input, including the full kitty keyboard protocol round-trip.
- `Parser` has a fifth state (APC, `ESC _ ... ESC \`) feeding `src/puppy/graphics.py`'s
  `GraphicsManager` (`a=T`/`a=t` transmit, direct RGB/RGBA only, chunked reassembly
  included) via `Screen.graphics_command`, and `src/puppy/render/graphics_renderer.py`'s
  `GraphicsRenderer` actually draws what it accumulates (real textured-quad GPU pass).

### This session had two phases. Phase 1 (GPU image rendering + launcher) is covered
### by the Milestones entries below. Phase 2 is a real live user bug report, acted on
### immediately — this is the part that matters most for picking up next.

**The user actually launched puppy live for the first time this session** (via the new
wrapper, not yet via wofi itself) and reported 5 concrete problems in one message:
"things arent rendering properly, font is too big, its not themed, launch is very slow,
and it doesnt close with mod q as if its not listening to niri commands." All five were
investigated by reading real source (this codebase's, rendercanvas's, and checking this
system's real niri config) rather than guessed at. Three have real, source-confirmed
root causes that are now fixed; one is measured and diagnosed with no quick fix
identified; one (theming) was simply never built and now is.

**Phase 3 (same day, next conversation): user live-tested the phase-2 fixes.** Results —
Mod+Q **confirmed fixed**, closes correctly now. Theming **partially confirmed** ("kinda
working" — not fully verified, not re-investigated this pass, revisit next). Sizing/
rendering **still broken** — screenshot showed garbled/split text and large solid-color
blocks. User's own guess was "maybe more protocols are needed." **That guess was
checked and ruled out**, not just dismissed: reproduced the corruption directly against
`Screen`+`PtySession`+`Parser` with zero GPU/rendering code involved (fed real
`unifetch` output through the actual PTY layer, dumped the grid) — the corruption
showed up in the **screen model itself**, not the renderer, and disappeared entirely
when the same repro ran at a *fixed* size with no resize event. That isolates it to a
timing bug, not missing protocol coverage. Root cause: `app.py`'s `run()` created
`Window` (which gets its real niri-assigned size immediately — confirmed via a direct
probe, `glfw.get_framebuffer_size()` already returns it right after window creation, no
settling/poll_events loop needed) but then still spawned `PtySession`/`Screen`/
`CellRenderer` at the function's *stale default* `rows=24, cols=80`, relying on the
async framebuffer-resize callback (the phase-2 fix) to correct it later. The shell's rc
file auto-runs `unifetch` immediately on spawn, so it starts printing at the wrong
80-column width; when the resize callback's `screen.resize()` then fires mid-print, the
grid rewraps at a *different* column boundary than the already-in-flight output
assumed, chopping/duplicating lines — reproduced exactly this way (inject a
`screen.resize()`/`session.resize()` call ~150ms into a real `unifetch` run) before
touching any fix code, and confirmed the corruption vanishes when the correct size is
used from the start instead. Fix: `Window.get_framebuffer_size()` (new, `window.py`) is
now called in `app.py`'s `run()` **before** `PtySession`/`Screen`/`CellRenderer` are
constructed, so the shell never starts at the wrong width in the first place — the
resize callback still exists and still matters for genuine *later* interactive resizes,
it's just no longer doing double duty as the fix for the initial-size race. Verified:
248 tests still pass; the headless Screen/PtySession repro (bypassing the GPU
entirely) now renders `unifetch`'s real output cleanly at the correct queried size; a
`timeout 5 python -m puppy.render.app` smoke test still starts/runs 5s with no
crash/traceback.

**Phase 4 (same conversation, continued): user live-tested the phase-3 fix.**
Sizing/rendering **confirmed fixed** — screenshot showed cleanly aligned, non-garbled
text, no more split/duplicated lines. Mod+Q still confirmed working. User asked two
things: (1) is theming "still not its strong suite," and (2) how far along is puppy
overall / is anything left before moving to personalizing it, noting it's "not on par
with kitty, ghostty or even OdyTTY." The maturity question is legitimate and worth
answering honestly next session — puppy has real breadth (baseline VT100/xterm, 256/
truecolor, scrollback, alt-screen, kitty keyboard protocol w/ query-response, kitty
graphics protocol w/ real GPU image rendering, real bold/underline) but is still
finding "real window, real timing" bugs each live-test pass, which is normal for a
project at this stage but does mean it isn't at daily-driver parity yet — say this
plainly if asked again, don't oversell it.

**Theming bug — RESOLVED as non-reproducing (2026-08-22), after finishing the two
untested hypotheses the prior session left open.** Recap: user's screenshot had shown a
white/pale background behind printed cells (including trailing padding spaces) while
untouched grid regions stayed correct navy; the `Screen` model and one-shot offscreen
GPU pipeline had both already been proven clean against the real captured byte stream,
narrowing it to something live/frame-loop specific. This session closed out the two
remaining untested hypotheses and found neither reproduces:
- **(b) multi-frame queue-sync**: fed the *exact* real chunked byte stream (49 real
  `select()`-sized chunks captured from a live `tish`+`unifetch` PTY session, not a
  single write) through the exact live per-frame pattern (`parser.feed()` →
  `build_instances()` → `canvas.request_draw()` → `canvas.draw()`, once per chunk, 49
  real frames) against an offscreen canvas. Zero white/pale pixels on readback — the
  offscreen pipeline is clean even under the exact live-matching multi-frame conditions,
  bold-heavy `unifetch` output included.
- **(c) `CellRenderer.resize()` reuse**: launched the real live GLFW window twice via the
  existing grim/niri screenshot technique (see below) — once at initial launch, once
  after driving real niri resize actions mid-session (`maximize-column`, `set-column-
  width -25%`, then `+40%`) to force `resize()` to actually fire and reuse its buffers.
  Both captures pixel-sampled with zero white/pale pixels; dominant colors matched the
  theme's `(0, 0, 41)` bg / `(159, 182, 205)` fg exactly.
- **Conclusion**: the bug does not currently reproduce under any of the previously-live-
  only or previously-untested conditions. Root cause remains genuinely unknown — this
  could mean it was an artifact of some transient system/GPU-driver state at the time of
  the original report, or a condition still not hit (sustained real interactive use
  rather than a passive/scripted launch). Not claiming "definitely fixed" since no code
  changed here and no root cause was found, but extensive, rigorous, real (not just
  eyeballed) pixel-level testing across both suspicious scenarios found nothing. If it
  recurs, use the pixel-sampling method now proven here (grim screenshot → PIL/numpy →
  count `r>180 and g>180 and b>180` pixels) rather than eyeballing a screenshot.

**Live-render debugging technique (new this session, reusable)**: to inspect what's
*actually* on screen in the live GLFW window without repeatedly focusing/closing
windows on the user's desktop (disruptive, see the standing rule about this) —
`(timeout N python -m puppy.render.app &)`, poll
`niri msg --json windows` in a loop for a window with `"title": "puppy"` to appear
(usually near-instant), then a single `grim` call for a full-output screenshot,
read via the Read tool. One launch, one shot, no manual interaction. Remember to set
`SHELL=/home/teter/.local/bin/tish` explicitly when the repro needs to match the user's
real shell (this machine's default `$SHELL` in a plain bash tool session is
`/bin/bash`, which won't auto-run `unifetch` the way the user's real launches do).

1. **"doesn't close with Mod+Q" — root cause definitively confirmed, fixed.** Not a
   niri/Wayland compatibility problem. `app.py`'s main loop calls `glfw.poll_events()`
   directly (raw GLFW, chosen for real key-repeat events — see the existing Architecture
   decision on bypassing rendercanvas's input abstraction). But
   `rendercanvas.glfw.GlfwRenderCanvas`'s *own* close-detection
   (`glfw.window_should_close()` -> `self.close()`) only runs inside its private
   `_rc_gui_poll()` method, which is called from rendercanvas's own event-loop
   machinery — never from a raw `glfw.poll_events()` call. So `Window.should_close()`
   (which just read `canvas.get_closed()`) could never become true from a WM close
   request; a Mod+Q close request genuinely did nothing detectable, no matter how many
   times it was sent. Confirmed by reading `rendercanvas/glfw.py`'s real source
   (`_on_want_close` is a no-op notification callback; `_maybe_close()`, the only place
   that actually checks the flag, is only called from `_rc_gui_poll`). Fix (`window.py`,
   `Window.should_close()`): also check `glfw.window_should_close(self._glfw_handle)`
   directly — GLFW itself sets that flag automatically on any WM/compositor close
   request (standard cross-platform GLFW behavior, not Wayland-specific), puppy just
   never looked at it. Self-contained, doesn't depend on rendercanvas's private `_rc_*`
   polling internals.
2. **"font is too big" + "rendering not properly" — one shared root cause, confirmed via
   source + this system's real niri config, fixed (but incompletely — see the phase 3
   entry above for the second, deeper bug, a PTY startup-size race, this alone didn't
   catch).** `puppy.render.app` never handled
   window resize at all (confirmed: zero resize wiring existed anywhere in `render/`).
   `CellRenderer` computed a fixed `screen_size` uniform once at construction from
   `cols*cell_width x rows*cell_height` and never updated it. Checked this system's real
   `~/.config/niri/profiles/null.kdl`: `default-column-width { proportion 0.5; }` — niri,
   like most tiling compositors, does **not** honor an app's requested initial window
   size; it sizes new windows from its own layout policy instead. So the *actual* wgpu
   surface puppy got was essentially guaranteed to differ from the size its GPU math
   assumed, and the fixed 80x24 grid got stretched (via the NDC math in
   `cell_renderer.py`'s vertex shader) to fill whatever surface niri actually gave it —
   almost certainly bigger, since 0.5 of a 1920px-wide output (960px) is roughly 1.2x
   the ~800px the default 80-column grid at 16px cells would request. This one geometric
   bug plausibly explains both symptoms at once (upscaled glyphs read as "too big",
   sampling a fixed-resolution glyph atlas at a stretched scale reads as "not rendering
   properly") rather than being two separate bugs — a materially better explanation than
   "the default font size number is wrong" (checked: 16px at this system's 96dpi/scale-1
   setup is the *same* effective size as this system's real kitty.conf default,
   `font_size 12` pt ≈ 16px — so 16 wasn't an unreasonable choice on its own). Fix:
   - `CellRenderer.resize(rows, cols)` (`cell_renderer.py`) — reallocates the instance
     buffer and pushes a corrected `screen_size` into the uniform buffer for a new grid
     shape. Required refactoring `__init__` to keep the bind-group layout, underline
     metrics, and uniform buffer around as real attributes instead of `__init__`-local
     variables, since `resize()` needs to reuse them without rebuilding the whole
     pipeline/shader/atlas-texture (those don't change on resize, only the grid does).
   - `Window.set_framebuffer_size_handler(handler)` (`window.py`) — a new raw GLFW
     callback wrapper (`handler(width, height)` in real pixels), chained *after*
     rendercanvas's own framebuffer-size callback (peeked via
     `glfw.set_framebuffer_size_callback(handle, None)`'s return value, which is
     confirmed empirically to return the previously-registered Python callback — GLFW
     only supports one callback per event type, they don't stack) so rendercanvas still
     gets to reconfigure the wgpu surface itself; puppy's own resize logic runs after.
   - `app.py`'s `run()`: a new `on_resize(width, height)` closure computes
     `new_cols = width // cell_width`, `new_rows = height // cell_height`, and if
     different from the current grid, calls `screen.resize()`, `session.resize()` (PTY
     `TIOCSWINSZ`, triggers the kernel's own `SIGWINCH` to the child automatically —
     already existed, just was never called after startup), and `renderer.resize()` in
     that order. `draw_frame()` and `GraphicsRenderer.render()` now read `screen.cols`/
     `screen.rows` (already the live, current values, same as `build_instances` already
     did) instead of the closed-over initial `cols`/`rows` parameters.
   - 2 new exact-pixel GPU-readback tests in `test_render_cell_renderer.py`
     (`test_resize_grows_grid_and_new_cells_render_correctly`,
     `test_resize_to_same_dimensions_is_a_no_op`) prove `CellRenderer.resize()` itself
     is correct in isolation. **What's NOT proven yet**: that the full live
     window -> GLFW callback -> `on_resize` -> `screen.resize`/`session.resize`/
     `renderer.resize` chain actually fires correctly end-to-end when niri really
     resizes the window — a `timeout 5 python -m puppy.render.app` smoke test showed no
     crash/traceback while presumably being resized by niri on open, which is
     reassuring but not the same as visually confirming the grid now fills the window
     correctly at the right glyph size.
3. **"its not themed" — real gap, never built before, now built.** puppy had hardcoded
   white-on-black (`DEFAULT_FG`/`DEFAULT_BG`) and no connection to RengeOS's 586-theme
   theme-switcher system at all. New `src/puppy/render/theme.py`
   (`load_theme()`/`Theme`): finds the active theme by resolving
   `~/.config/theme-switcher/wallpaper` (a real per-theme symlink theme-set.sh
   regenerates on every switch — confirmed against the real script; used instead of
   theme-set.sh's own `current_theme()` mechanism, which resolves via the waybar
   profile's symlink instead, since that would make puppy depend on waybar being
   installed/running for no real reason — both resolve to the same theme directory)
   and parses that theme's real `kitty-colors.conf` (every theme-switcher theme already
   ships one, since kitty's own theme format is just plain `key value` pairs —
   confirmed against the live `midnight2` theme's actual file, no format surprises).
   Wired into `app.py`'s `run()`: the 16 ANSI colors are pushed into
   `Screen.set_palette_color(0..15, spec)` at startup — exactly the same mechanism a
   program setting its own theme via real OSC 4 would use, so `resolve_color()` (already
   tested, already the one true color-resolution path) needed zero changes — and the
   theme's `fg`/`bg` are passed into `build_instances()` as the new `default_fg`/
   `default_bg` params (defaults preserved for existing tests/callers). Falls back to
   plain white-on-black if theme-switcher isn't present at all (portable off this one
   machine). 6 new tests (`test_render_theme.py`, real temp-directory fixtures, no
   dependency on this machine's actual live theme files) plus a manual live check
   against the real active theme (`midnight2`) confirmed correct parsing (`fg=(159, 182,
   205)`, `bg=(0, 0, 41)`, all 16 ANSI colors). **Not yet visually confirmed live** —
   parsing is proven correct, but no one has watched the window actually render in
   theme colors instead of white-on-black yet.
4. **"launch is very slow" — measured, diagnosed, no quick fix identified, documented
   honestly rather than guessed at.** Timed every phase of `puppy.render.app`'s startup
   directly in this session (not estimated): `import puppy.render.app` ~0.25s (covers
   all heavy library imports: numpy, wgpu-py, uharfbuzz, freetype-py, glfw,
   rendercanvas), `find_monospace_font()` (an `fc-match` subprocess call) ~0.01s,
   `FontRenderer` init ~0s, `Window` init (real GLFW window creation + real wgpu
   adapter/device negotiation) **~0.53s**, `CellRenderer` init (WGSL shader
   compilation + buffer/bind-group setup) **~0.29s**, `GraphicsRenderer` init ~0.02s,
   `PtySession` spawn ~0s. **Total ~1.1s**, with ~0.82s of that (75%) being wgpu
   adapter/device negotiation + shader compilation — both real, inherent one-time GPU
   bring-up costs of the wgpu/GLFW architecture this project deliberately chose (see the
   existing rendering/windowing toolkit Architecture decision), not obviously fixable
   without either caching a compiled pipeline across process launches (wgpu-native's
   underlying Rust crate has a pipeline-cache concept; whether wgpu-py's Python bindings
   expose it wasn't checked this session) or restructuring the GPU init sequence
   (unexplored). $SHELL on this machine resolved to `/bin/bash` (not `tish`) when
   checked, and a non-interactive `tish -c exit` timed at 8ms — so a slow child-shell
   startup was checked and ruled out as a contributing factor, at least for the
   non-interactive case; interactive tish startup (with its real prompt/RC-file
   pipeline) wasn't separately timed and remains a small unverified gap. **No code
   change was made for this one** — 1.1s is slower than a native terminal (kitty/xfce4-
   terminal are typically 150-300ms) but isn't obviously *broken*-slow, so this is
   reported as a measured, honest finding for the next session to decide whether it's
   worth chasing further (wgpu-native pipeline caching would be the concrete next thing
   to check), not something guessed at or silently left undocumented.

248 tests passing (up from 110 at the very start of the rendering push; 240 at the end
of this session's phase 1, 8 more from phase 2's fixes — 6 `test_render_theme.py` + 2
new `test_render_cell_renderer.py` resize tests). `source .venv/bin/activate` for any
code work.

**Verified this session (phase 2)**: full test suite green (248 passed); a fresh
`timeout 5 python -m puppy.render.app` live run still starts and runs the full 5 seconds
with no crash/traceback (only the pre-existing, harmless `libdecor-gtk-WARNING`),
importantly including through whatever resize niri applied on open, since the resize
path is now live code that runs on every launch, not just in offscreen tests.

**Known gaps, all deliberate and documented** (not oversights): kitty keyboard protocol
has no alternate-key/text-embedding subfields, no hyper/meta modifiers, no push/pop
flags stack (single flat value); legacy encoding has no Alt/Meta-prefixed sequences, no
Ctrl+non-letter combos, no Shift+function-key variants; no strikethrough or kitty's
HSLuv-based automatic contrast override; kitty graphics has no PNG/compression/delete/
query/animation/unicode-placeholder/file-transmission support; graphics rendering has
no z-index layering (images always draw on top, in placement order), no cropping
(`src_rect` is always the full image), no scrollback-scroll tracking for images, and
one uniform-buffer+bind-group allocation per placement per frame (no batching —
fine for real placement counts, revisit only if profiling shows otherwise); no theming
for anything except the base 16 ANSI colors + default fg/bg (cursor color, selection
color, and the theme's own `cursor_text_color`/`selection_*` keys from
`kitty-colors.conf` are parsed by nothing yet — puppy doesn't have cursor/selection
rendering built at all regardless of theme).

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
      runs with no crash. **Confirmed with a real live image 2026-08-22** —
      see the PNG milestone entry directly below; a hand-crafted APC sequence
      rendered a real gradient PNG correctly inside the actual live window.
- [x] **Kitty graphics: PNG format (`f=100`) + zlib compression (`o=z`) — 2026-08-22.**
      Confirmed against kitty's real `png-reader.c`/`graphics.c` before building:
      kitty links real libpng and always normalizes decoded output to RGBA8
      regardless of the PNG's own color type/bit depth (palette, gray, 16-bit, tRNS
      alpha, etc. all get folded to RGBA8 via `png_set_*` calls in
      `inflate_png_inner`); the PNG's *own* header width/height unconditionally
      *override* whatever `s`/`v` the client declared (`load_data->width = d.width`
      in `inflate_png`) — kitty doesn't even require `s`/`v` for `f=100`, confirmed
      neither does puppy now. Compression (`o=z`, zlib) is applied *before* PNG
      decode when both are present (confirmed via `process_image_data`'s real
      compressed-then-format switch ordering) and is independent of format —
      `f=24`/`f=32` payloads can be `o=z`-compressed too, decompressed then
      length-checked against the declared `width*height*bpp` exactly as the
      uncompressed path already was. Sizing: PNG/compressed loads have no exact
      expected size upfront (unlike uncompressed direct RGB/RGBA), so accumulation
      is capped at kitty's own real `MAX_DATA_SZ` (`4u * 100000000u`, confirmed via
      graphics.c's literal `#define`) instead of an exact match, with the real
      decompressed/decoded size validated only after the last chunk arrives.
      PNG decoding itself uses Pillow (`Image.convert("RGBA")`) as the direct
      Python equivalent of kitty's libpng dependency — matching this project's
      established pattern of using real, proven libraries for infrastructure
      (HarfBuzz, FreeType, wgpu-native) rather than hand-rolling a PNG/zlib-chunk
      parser from scratch; new project dependency, `pyproject.toml` updated.
      Deliberately out of scope, matching kitty's own optional (not mandatory-decode)
      extras: ICC colour-profile transforms and embedded-gamma correction (kitty's
      PNG path additionally pulls in `lcms2` for this; puppy treats all images as
      already-sRGB, consistent with every other color path in this codebase).
      `Image.format`/`GraphicsRenderer` needed zero changes — PNG decode always
      resolves to the existing `format=32` (RGBA) path, so it flows through the
      already-tested GPU upload/render code unchanged. 15 new tests (10
      `test_graphics.py`: real-Pillow-generated PNG decode + dimension-override,
      PNG chunked reassembly, `o=z` round-trip for both RGBA and PNG, corrupt-PNG/
      corrupt-zlib/wrong-decompressed-size discarding, unknown-compression-value
      rejection, `MAX_DATA_SZ` cap enforcement via a patched-small constant, PNG
      oversized-dimension rejection; 1 `test_parser.py` end-to-end through a real
      APC PNG escape sequence). **Live-confirmed the same session** (see below,
      first-ever live image render) — not just proven via GPU-readback tests.
      **Live-confirmed real image render (2026-08-22, first time ever for this
      project)**: built a real 120x80 gradient PNG (Pillow), base64-encoded it into
      a real hand-crafted `\x1b_Ga=T,f=100,...;<payload>\x1b\\` APC sequence inside a
      wrapper shell script used as `$SHELL` (so the real live app spawns it, prints
      the sequence unprompted, then `exec`s into the real interactive shell) —
      launched via the existing grim/niri screenshot technique, screenshot showed
      the gradient rendering correctly, colors accurate, positioned at the cursor
      origin, composited correctly with the cell grid and theme background. This
      closes the milestone's last open item ("no one has watched an actual image
      appear in the live window yet") for direct RGB/RGBA *and* PNG at once, since
      the same GraphicsRenderer path handles both.
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
- [x] **First real live user bug report, acted on same session (2026-08-20).** User
      launched puppy for the first time and reported 5 problems in one message: not
      rendering properly, font too big, not themed, slow launch, doesn't close with
      Mod+Q. Full diagnosis + fixes for all 5 are in the Current status entry above
      (kept there rather than duplicated here since it's the fuller writeup) —
      summary: (1) Mod+Q root-caused to `app.py` bypassing rendercanvas's own
      close-detection by calling raw `glfw.poll_events()`, fixed in
      `Window.should_close()` by checking `glfw.window_should_close()` directly;
      (2) font-too-big/rendering-wrong root-caused to a total absence of resize
      handling combined with niri's real `default-column-width { proportion 0.5; }`
      policy overriding puppy's requested window size on every single launch, fixed
      via `CellRenderer.resize()` + `Window.set_framebuffer_size_handler()` +
      `app.py`'s new `on_resize` closure; (3) theming built from scratch
      (`src/puppy/render/theme.py`, real `kitty-colors.conf` parsing, wired via the
      existing OSC-4-override `Screen.set_palette_color` mechanism); (4) launch
      slowness measured phase-by-phase (~1.1s total, ~75% wgpu adapter negotiation +
      shader compilation, both real one-time GPU-bringup costs of the chosen
      architecture) with no fix applied, documented as a measured finding for a
      future session to decide on; (5) not applicable, was symptom of (2). 8 new
      tests (6 `test_render_theme.py`, 2 new resize tests in
      `test_render_cell_renderer.py`), full suite green (248 passed), a fresh live
      `timeout 5` run showed no crash through the resize path. **None of the 3 code
      fixes have been visually confirmed by the user yet — this is the single most
      important thing to check at the start of the next session**, before starting
      any new feature work. See Current status for the full per-symptom writeup;
      this entry will stay in Milestones as the permanent record after Current
      status gets replaced by a future update.

## File map

```
puppy/
  PROGRESS.md          this file
  pyproject.toml        now declares glfw/wgpu/rendercanvas/uharfbuzz/freetype-py/pillow
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
      theme.py                        load_theme() — reads RengeOS's active theme-switcher colors
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
    test_render_theme.py    kitty-colors.conf parsing, real temp-dir fixtures, no live-machine dependency

~/.local/bin/puppy                  wrapper: cd + activate .venv + exec python -m puppy.render.app
~/.local/share/applications/puppy.desktop   wofi/launcher entry, Categories=TerminalEmulator
```

## Next steps (pick up here)

**Before anything else: `source .venv/bin/activate`** — the render layer's
dependencies live there, not in system Python. `pip install -e .` again if the venv
is ever recreated (it's gitignored).

**Nothing is mid-flight; there is no unfinished code to pick back up.** Everything
through the 2026-08-20 bug-fix pass (Mod+Q close, resize/theming) is real, committed,
and test-covered. But **none of this session's 3 code fixes have been visually
confirmed live** — that is the mandatory first step of the next session, not an
optional nice-to-have, because it's entirely possible one of the diagnoses was wrong
or incomplete and there's more to find.

**2026-08-22 session recap (so the next session doesn't redo this work)**: item 0 below
(the mandatory live-confirmation pass) is now done — Mod+Q, sizing, and theming were all
live-checked via the grim/niri screenshot technique and look correct (theming's prior
white-background report specifically does not reproduce, see Current status for the full
non-repro writeup; treat as "clean for now," not "provably fixed," since no root cause
was ever found). PNG (`f=100`) + `o=z` compression are now implemented, tested, and
live-confirmed with a real rendered image for the first time ever. Launch-speed
(~1.1s, wgpu bring-up dominated) was not re-measured this session, no new information.

0. ~~DO THIS FIRST~~ — **done 2026-08-22**, see the recap above and Current status for
   detail. Still open from the original checklist: whether launch feels *dramatically*
   slower than the measured ~1.1s in real sustained use (only short `timeout`-bounded
   launches have been tried so far, not a long real session) — worth a passing check
   next time puppy is used for actual work, not a dedicated pass on its own.
1. **Kitty graphics: next real protocol milestone — pick one.** Each independent, see
   the `[ ]` Milestones entries above for the exact key/action list each needs:
   - `a=p`/`a=d`/`a=q` (put an already-transmitted image at a new position / delete /
     query without displaying) — smaller, self-contained additions to
     `GraphicsManager.handle_command`'s action dispatch. Probably the next one to reach
     for, since it's the smallest self-contained unit of the three.
   - z-index layering — currently images always draw on top of everything in
     placement order; real programs (e.g. anything drawing a background image) rely
     on negative z-index drawing *below* the cell grid, which needs `GraphicsRenderer`
     to interleave with (or run before) `CellRenderer` rather than always running
     after it — a real architectural change to `app.py`'s `draw_frame()`, not just a
     `GraphicsManager` addition. Check kitty's real z-index tiers (below/negative/
     positive, confirmed present in `grman_update_layers`, not yet re-read in detail)
     before designing this one.
   - Sixel graphics (fallback/parity with non-kitty terminals) — a materially different
     protocol from kitty's own, bigger and more self-contained than the two above.
2. Separately, whenever there's a spare cycle, unrelated to graphics: live-test
   `TERM=puppy python -m puppy` (the *text* pass-through harness, `__main__.py`) in a
   real terminal window (a real ncurses/vim session using the terminfo entry, not just
   `curses.setupterm()` accepting it headlessly) — still pending since it was first
   noted, needs an interactive session.

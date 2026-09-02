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

## Current status (2026-09-03, launch time -- early background paint, real perceived win)

Follow-up to the shader-cache-theory-refuted entry directly below: with that ruled out,
picked the other real, previously-unattempted option instead of stopping at "accept it"
-- deferring first paint past `CellRenderer`'s pipeline-creation cost, per the user's
"try them" (both remaining options were on the table; the actionable one is this).

`render/app.py`'s `run()` now does one extra `window.gpu.clear(srgb_color(*theme.bg))` +
`force_draw()` immediately after `Window()` returns, *before* `CellRenderer`/
`GraphicsRenderer` are constructed. `GpuContext.clear()` (already existed, from the
project's very first rendering milestone) needs no shader pipeline of its own -- it's a
plain load_op-clear render pass -- so this costs only the clear+present cycle itself, not
the ~0.3s `CellRenderer` shader compile.

**Measured live** (real GLFW window, launched via `python -m puppy.render.app`,
auto-terminated after 2.5s with `SIGTERM` -- a deliberate, one-time, single launch for
this verification, not a repeated open/close cycle): temporary stderr timestamps at each
stage, removed again after confirming (not left in the shipped code) --

```
window created:          t=+0.000s
early paint presented:    +0.108s   (previously: window sat undefined here)
renderers ready:          +0.440s   (CellRenderer + GraphicsRenderer pipeline creation)
entering main loop:       +0.444s
first real text frame:    +0.475s
```

Confirms the real effect: the window now shows the correctly-themed background ~0.33s
before it otherwise would have (whatever undefined content an unpainted wgpu swapchain
gives the compositor, previously visible for that entire stretch). **Total wall time to
a fully functional, text-rendering terminal is unchanged** — this is a genuine
perceived-latency improvement, not a fix to the underlying ~0.3s shader-compile cost,
exactly as scoped going in. 394 tests still passing (no test coverage added — this is a
`run()`-only live-window behavior change with no headless-testable surface, same
category as the rest of `render/app.py`).

**Still true, now for a stronger reason to just accept it**: total cold launch remains
~1.1-1.5s. With the shader-cache theory refuted and this perceived-latency mitigation
now in place, there is no remaining actionable lead for reducing the *actual* GPU
adapter/device (~0.55s) or shader-pipeline (~0.3s) costs themselves — both are genuine
wgpu/Vulkan work this from-scratch Python renderer has no lever over short of a much
larger architecture change (e.g. the previously-flagged, real-regression-risk GL-backend
experiment against the NVIDIA adapter). Recommend treating this as closed unless a new
concrete lead shows up.

## Current status (2026-09-03, launch time -- shader-cache theory tested, REFUTED)

User asked "why is puppy still doing a slow launch" after the 2026-09-02 fix, prompting
a re-check. **The NVIDIA-wake fix itself is not regressed** — re-read `gpu.py`, confirmed
`set_instance_extras(backends=["Primary"])` is still in place, unchanged since 2026-09-02.
Total launch is still real, cold, honestly ~1.1-1.5s (0.52-0.57s GPU adapter/device
bring-up + 0.29-0.36s shader-pipeline creation + ~0.15s everything else + ~0.35s Python
interpreter/venv startup outside the instrumented range) — that number was never claimed
fixed, only the *extra* ~1.7s on top of it from the NVIDIA GL-probe wake was.

Directly tested the "re-profiled" entry's own open, explicitly-unconfirmed lead (shared,
evictable Mesa shader cache explains the previously observed 1.1s-best/2.5s-worst
variance) since the user picked "confirm the shader-cache theory" over the other two
options offered (defer first paint past shader compile; accept as environmental cost).
**Result: refuted, not confirmed.** A/B across `MESA_SHADER_CACHE_DISABLE=true` (always
forces a real recompile, no caching at all), a brand-new empty `MESA_SHADER_CACHE_DIR`
on its first-ever hit (genuinely cold, isolated from every other app's cache traffic),
and the same isolated dir on a second hit (genuinely warm, same isolation) all landed at
the *identical* ~0.30-0.31s for the shader-creation step, plus 4 back-to-back fresh-
process trials against today's real shared cache all landing at ~1.08-1.16s total with
no first-in-burst spike. If shader-cache eviction were the explanation, disabling the
cache entirely (guaranteed-cold on every run) should have looked like the isolated-cold
case and both should have clearly cost more than the isolated-warm case — instead all
three were indistinguishable. Whatever this specific shader/pipeline actually costs to
create, a disk cache isn't measurably helping or hurting it either way here.

**Consequence**: the original 2026-09-02 "re-profiled" finding's ~2.0-2.5s
first-in-a-burst worst case did not reproduce today under otherwise-similar conditions
(same machine, same code, same shared-cache state) — either it's genuinely
environmental/transient (a specific desktop GPU-idle state at the time, since Intel's
iGPU — the adapter puppy actually uses, confirmed 2026-09-02 — can have its own
runtime-power-management wake cost distinct from the already-fixed NVIDIA one, and
today's desktop simply wasn't in that state during any trial) or it depended on system
conditions not reproduced by this session's tests. Not chased further per the same
"real candidates, none picked unilaterally" posture as everywhere else in this file —
the two remaining real options are the same two the user didn't pick this time:
(a) restructure `run()` to show the window before `CellRenderer`'s pipeline creation
finishes, so the ~0.3s becomes invisible to the user even though it's still spent, or
(b) accept ~1.1-1.5s cold-launch as this architecture's real, honest cost (a from-scratch
wgpu-rendered terminal doing real Vulkan adapter/device/pipeline setup in Python, next to
compiled-native terminals that pay none of that) and stop chasing it.

## Current status (2026-09-02, kitty graphics animation: a=f/a=a)

User picked "Animation (a=a/a=f)" off the standing candidate list (vs. keybind config,
tabs/splits, Sixel) when asked which feature to build next, matching this file's own
"real candidates, none picked unilaterally" note in the prior Next-steps entry.

Added the two remaining kitty-graphics actions the 2026-09-02 completeness pass below
explicitly scoped out as a separate, larger subsystem: `a=f` (transmit animation frame
data -- a new frame by default, or edit an existing one via `r=`, composited onto a
background canvas that's either a previous frame's data, the frame being edited itself,
or a solid `Y=` color) and `a=a` (animation control: `c=` jumps to a frame client-side,
`s=` starts/stops terminal-driven playback with `2`=run-but-freeze-at-the-end vs.
`3`=run-and-loop, `v=` sets the loop count, `r=`+`z=` sets an existing frame's gap --
the only way to give the root frame a nonzero gap, since it has none by construction).
`a=c` (compose two *already-existing* frames -- a distinct, rarer feature from the
frame-loading compositing `a=f` itself does) stays out of scope, same as before.

Read kitty's real `graphics.c` (`handle_animation_frame_load_command`,
`handle_animation_control_command`, `scan_active_animations`, the `Frame`/`Image`
struct fields in `graphics.h`) and the protocol doc's own animation section
(`docs/graphics-protocol.rst`) before writing anything, not from memory -- caught
several nonobvious real behaviors this way, all now covered by tests: frame numbers are
1-based and uniform across root+extra frames (`c=1`/`r=1` mean the root frame, in both
`a=f` and `a=a`); a frame's gap floors negative ("gapless") values to `0`, and `z=0` on
a *new* frame means "use the default 40ms", not "gap of exactly zero" -- there is no way
to request a literal zero gap except the negative/gapless form; terminal-driven playback
only actually advances when the image has at least one extra frame *and* the sum of
every frame's gap is nonzero (an all-gapless animation just never plays, rather than
busy-looping forever with no visible effect -- confirmed this is kitty's own real
`image_is_animatable` gate, not an edge case worth skipping); gapless frames are hopped
over within a single tick until landing on one with a real gap; `s=2` (loading) freezes
on whatever frame is current when it would otherwise wrap, rather than looping, and
correctly resumes the instant a new frame arrives; and reaching `v=`'s loop limit
freezes the animation on its *current* frame at the point the wrap is refused -- it does
**not** perform one final wrap back to the root frame first (traced this precisely
against `scan_active_animations`'s do-while: the `goto skip_image` on a refused wrap
happens *before* `img->current_frame_index = next`, not after).

One deliberate simplification, not a bug: every frame is stored fully composited at
image width*height RGBA8 the moment it's transmitted/edited, not kitty's lazy
`base_frame_id` reference-chain representation that only fully renders a frame when
something actually needs its pixels. This project already treats "don't pre-optimize,
direct per-item work is fine at real-world scale" as a standing performance philosophy
(see `puppy.render.graphics_renderer`'s own module docstring on draw-call counts, and
the parser's byte-level state machine) -- real terminal animations (a spinner, a small
sprite) are tiny compared to what kitty's reference-chain optimization actually exists
for (long, video-like animations past a real length threshold it special-cases). If
that assumption ever turns out wrong for something the user actually wants to run,
switching a specific frame's storage to lazy/on-demand compositing is a contained change
inside `_finalize_animation_frame`/`_frame_array`, not a rearchitecture.

Compositing math (straight-alpha "over", matching kitty's real `alpha_blend()`) is
implemented with real vectorized numpy array ops in `GraphicsManager._composite`, not a
nested Python pixel loop -- `puppy.graphics` didn't depend on numpy before this pass
(kept deliberately minimal, stdlib + Pillow only for the PNG path), but a pure-Python
double loop would make a 10000x10000 `MAX_IMAGE_DIMENSION`-sized animated image (a legal,
if extreme, transmission) audibly slow; numpy was already a hard dependency of the
render layer regardless, so this doesn't add a new dependency to the project, just to
this one module.

`GraphicsManager.tick(now)` is the new piece the render layer must actually call --
owns only *which* frame is current for each image, not drawing it. Wired into
`render/app.py`'s existing `draw_frame()` via `screen.graphics.tick(time.time())`,
using the same wall clock the cursor blink already reads there. No new scheduling
machinery was needed: this project's render loop already `force_draw()`s
unconditionally every iteration (a ~100Hz busy loop gated only by the PTY-read
`select()` timeout), the same reason cursor blink needed no timer of its own --
confirmed by reading `run()`'s main loop before assuming a frame-pacing mechanism
was needed.

`render/graphics_renderer.py`'s per-image texture cache is now keyed on
`(image_id, frame_number)` instead of image identity alone (an animated image is one
long-lived `Image` object whose `current_frame_index` changes, not a new object per
frame) -- the identity check on the cached data bytes still catches in-place frame
edits (`a=f,r=`) correctly, since compositing always produces a new bytes object,
confirmed via a real GPU pixel-readback test (`test_editing_a_frame_in_place_
invalidates_the_cached_texture`) that edits a displayed frame and checks the next
render actually shows the new color, not a stale upload.

**Test coverage**: 22 new tests (394 total, all passing) -- 20 in `test_graphics.py`
covering `a=f` (new frame, edit-in-place, `c=`/`r=`/`X=`/`Y=` base-canvas and
compose-mode combinations, gap resolution including the `z=0` vs. `z=-1` distinction,
ENOENT/EINVAL error responses), `a=a` (client-driven jump, state transitions, loop-count
encoding, root-frame gap-setting), and `GraphicsManager.tick` (basic advance-and-loop,
the loading-state stall-then-resume case, max-loops freeze-without-final-wrap, gapless-
frame skipping, the all-gapless no-op case, and stopped/never-started no-op) -- plus 2
real-GPU pixel-readback tests in `test_render_graphics_renderer.py` (renders the current
frame rather than always the root; re-uploads after an in-place frame edit). Also ran a
one-off manual integration script feeding real escape-sequence bytes through
`Parser.feed()` -> `Screen.graphics_command()` -> `GraphicsManager` end to end (not kept
as a test file, just a sanity check that control-key case-sensitivity — e.g. `X=`
staying distinct from `x=` — survives the real APC parser, not just direct
`handle_command()` calls) -- confirmed clean.

**Not done, and not a small add-on to this pass** (same conclusion the prior session
reached, now with the actual implementation experience behind it): `a=c` (compose)
operates on two frames that already fully exist, entirely separate from `a=f`'s
transmit-and-composite path, and needs its own EINVAL/ENOENT/ENOSPC-on-overlap
validation per the spec's compose section -- a real, contained, but separate follow-up
if animation work continues. Visual/interactive confirmation (an actual multi-frame GIF
or hand-crafted spinner playing correctly in a live puppy window) has **not** happened --
same "real-but-only-smoke/unit-tested" category as the six items already listed in
Next-steps below, joining rather than replacing that list.

## Current status (2026-09-02, kitty graphics completeness: a=p/a=d/a=q, z-index)

User picked this off the standing candidate list (kitty-graphics completeness vs.
keybind config vs. tabs/splits/Sixel vs. the interactive-verification pass) rather than
the interactive-verification pass this file had recommended — noted, not overridden.
Mid-session the user also flagged launch time as "horrendous"; explicitly deferred
re-profiling it to after this pass finished (see the still-open 2026-08-20 entry further
down) rather than splitting focus.

**Built, tested (372 total, up from 346), and smoke-tested via a direct `Screen`
integration test (not just the GraphicsManager unit tests) — not yet visually confirmed
in a live window** (the desktop already had a puppy instance running this session; per
the live-window-testing rule, didn't touch it to do a visual pass):

- **`a=p` (put)**: displays an already-transmitted image by id, with `p=` placement id
  (repeating the same `p=` on the same image id replaces that placement rather than
  adding a second one — confirmed against kitty's real `handle_put_command`), `z=`
  z-index, explicit `c=`/`r=` cell span, and `x=`/`y=`/`w=`/`h=` pixel cropping.
- **`a=T`'s display step now goes through the exact same put logic `a=p` uses**
  (`GraphicsManager._place`), not separate code — confirmed this is what kitty's real
  `grman_handle_command` does too (the transmit command's own `p=`/`z=`/`c=`/`r=`/
  crop keys apply to its auto-display exactly like a following `a=p` would).
- **`a=d` (delete)**: `a`/`A` (all, `A` also frees image data), `i`/`I` (by image id,
  optionally narrowed by `p=`), `c`/`C` (at cursor), `p`/`P`/`q`/`Q` (at a given cell,
  `q`/`Q` also filtered by `z=`), `x`/`X`/`y`/`Y` (column/row), `z`/`Z` (exact z-index).
  Not supported, documented in `_handle_delete`'s docstring: `n`/`N` (by image
  *number* — number-based addressing was never built), `r`/`R` (id range), `f`/`F`
  (animation frame). **One real, documented approximation**: the point/column/row/z
  filters need each placement's actual rendered cell span to test coverage, which needs
  cell pixel dimensions — only the render layer has those, not the graphics model layer.
  An explicit `c=`/`r=` placement is matched exactly; an auto-sized one (`c=`/`r=` never
  given) is approximated as covering only its anchor cell. Matches real usage patterns
  (icat/chafa-style single-placement clears) correctly; a multi-cell auto-sized image
  targeted by an off-anchor point/column/row filter would miss, a real gap, not a bug in
  what's built.
- **`a=q` (query)**: runs a real image through the exact same chunked-transmission/
  decode path as `a=t`/`a=T`, responds OK/error, but never persists into `self.images`
  and never creates a placement — confirmed against kitty's own `remove_images` call
  right after responding to a query.
- **Command responses**: every action that produces one (`t`/`T`/`q`/`p`) now writes a
  real `\x1b_Gi=<id>[,p=<placement>];OK` or `;<CODE>:<message>` response back through
  `Screen._write_back` (already wired to the real PTY in both `render/app.py` and
  `__main__.py` — no new plumbing needed there), gated by `q=`/quiet the same way
  kitty's real `finish_command_response` is (`q=1` suppresses only the OK case, `q=2`
  suppresses everything). `a=d` never responds at all, matching kitty exactly (its
  `handle_delete_command` call site never assigns a response).
- **Z-index**: `Placement` now carries `z_index` (default 0); `GraphicsRenderer.render`
  draws `sorted(graphics.placements, key=lambda pl: pl.z_index)` (Python's stable sort
  keeps insertion order for ties) instead of raw placement order. Confirmed via a real
  GPU pixel-readback test: two overlapping placements, the higher-z one transmitted
  *first* still ends up on top.
- **Cropping now real for `a=p`-created placements** (`src_x`/`src_y`/`src_width`/
  `src_height` on `Placement`, resolved to a normalized texcoord `src_rect` at render
  time in `GraphicsRenderer.render` — previously always the full `[0,1]` identity rect).
  Confirmed via a real GPU pixel-readback test cropping a two-color image down to just
  one half.

**Explicitly still out of scope, not attempted this pass** (see `puppy/graphics.py`'s
module docstring for the authoritative list): `a=a`/`a=f` (animation — frame loading,
gaps, composition, timers; a genuinely large separate subsystem, not a small add-on to
this pass), `a=c` (compose), unicode-placeholder virtual placements, file/shm
transmission (`t=f`/`t=t`/`t=s`), image-number addressing (`I=`, and delete's
`n=`/`N=`/`r=`/`R=` variants that key off it), sub-cell pixel offsets (`X=`/`Y=` on
put), parent-relative placements (`P=`/`Q=` on put).

## Current status (2026-09-02, launch time -- ROOT CAUSE FOUND AND FIXED, in-code)

**Supersedes the "re-profiled" entry directly below, and this file's own prior
"ROOT CAUSE FOUND, closed" entry that preceded this one in the same session.**
That prior entry concluded the only fix was a system-wide NVIDIA power-management
setting and reported the user declining it, closing the investigation as an
accepted cost. **That conclusion was wrong** -- the user correctly pushed back
("if ur solution to a problem in puppy is to change smth else in my system then
the app is not being developed properly") and asked for the actual mechanism to
be understood, with research into how other GPU apps/libraries avoid this. That
push produced a real, in-code, zero-system-changes fix. Corrected in place here
per this file's own "replace stale entries" rule, not left standing alongside
the fix as a separate dead-end note.

**Real root cause, now precisely nailed down at the syscall level** (`strace -k`
with symbol resolution, not inferred): `wgpu.gpu.request_adapter_sync()`'s
underlying `wgpuCreateInstance` call -- at *instance creation*, before any
adapter-request filtering even runs -- unconditionally probes every backend
wgpu-native supports, including OpenGL/EGL. That GL/EGL probe loads NVIDIA's
GLVND vendor library (`libnvidia-glsi.so`) purely to enumerate it as a candidate
adapter, and that library opens `/dev/nvidia0` as a side effect -- which is what
wakes the NVIDIA GPU from PCIe runtime suspend on this hybrid Intel+NVIDIA
laptop (confirmed live: `power/runtime_status` flips `suspended` -> `active`
at exactly this call). All of this happens even though puppy always ends up
running on the Intel adapter via Vulkan and never touches GL or NVIDIA for
real work (confirmed via `enumerate_adapters_sync`: NVIDIA is only reachable
via OpenGL here, never Vulkan).

**Five Vulkan-loader-level env vars were tried first and every one failed**
(confirmed via `VK_LOADER_DEBUG=all` that each restriction genuinely took
effect at the Vulkan level, yet the wake still happened regardless) --
`VK_ICD_FILENAMES`, `VK_DRIVER_FILES`, `VK_LOADER_DRIVERS_SELECT` (the specific
fix found on a near-identical CachyOS/hybrid-GPU forum thread,
`discuss.cachyos.org/t/dgpu-wakes-up-unnecessarily-and-causes-delay/34362` --
same symptom, same Vulkan loader version, but their case involved the NVIDIA
*ICD* participating, a different code path than this one), `VK_LOADER_LAYERS_DISABLE`
(both NVIDIA's own implicit Optimus/present layers and Mesa's own
`VkLayer_MESA_device_select`), and Mesa's `NODEVICE_SELECT`. All of them operate
at or below the Vulkan *driver/ICD* level -- the actual cause sits one level up,
at wgpu-native's own `Instance` creation, which probes GL/EGL entirely
independently of any Vulkan-spec mechanism. `WGPU_BACKEND`/`WGPU_BACKEND_TYPE`
env-var guesses were also tried; a `strings` dump of the actual shipped
`libwgpu_native-release.so` confirmed neither is a real variable this build
reads at all (the real one, `WGPU_BACKEND_TYPE`, was later found by reading
wgpu-py's own `_api.py` source directly rather than guessing from general wgpu
knowledge -- confirmed being honored via its own log line -- but even that only
filters the *returned* adapter, not what the Instance probes at creation, so it
didn't help either).

**The actual fix: `wgpu.backends.wgpu_native.extras.set_instance_extras`**, a
real, public, documented wgpu-py API for exactly this -- it lets a caller
configure the wgpu-native `Instance` itself (via its `WGPUInstanceExtras`
native extension struct) *before* it's created, including which backends it's
allowed to probe at all. `puppy/render/gpu.py` now calls
`set_instance_extras(backends=["Primary"])` at module import time (see the
module's own comment for exactly why import-time, not lazily inside
`GpuContext.create()`: several test files probe `wgpu.gpu.request_adapter_sync`
directly in their own `_adapter_available()` skip-check, in the same module
that imports `GpuContext` -- import-time setup guarantees it runs before any of
those probes, since every one of those files imports this module first).
`Primary` = Vulkan/Metal/DX12/BrowserWebGPU, i.e. every non-legacy backend
except GL/GLES -- deliberately not hardcoded to `Vulkan`-only, since that would
be over-fitting to this one Linux machine's specific choice rather than the
real, portable distinction (GL is the actual problem; puppy never used it on
any platform).

**Confirmed live, repeatedly, with real ~15s idle gaps between each trial so the
dGPU had genuinely re-suspended each time** (not just re-tested back-to-back,
which would hide the effect): `power/runtime_status` for the NVIDIA PCI device
stayed `suspended` through every single trial post-fix (previously flipped to
`active` on 100% of cold trials pre-fix). `request_adapter_sync` alone dropped
from ~1.7-1.8s to a consistent ~0.09s; the full real app-init sequence
(imports through `GraphicsRenderer` construction, matching `run()`'s own
sequence) measured **~1.16s total on a genuinely cold trial** -- previously the
*best* case was ~1.1s and the worst was ~2.5s; now every trial, cold or warm,
lands at the old best case. No functional change -- puppy never used GL or the
NVIDIA adapter for real work either way, confirmed by every adapter-summary
print throughout this investigation still resolving to the same Intel/Vulkan
adapter as before.

372 tests still passing after the change (the module-level `set_instance_extras`
call is what makes the test suite pass too -- several render test files each
independently probe `wgpu.gpu.request_adapter_sync` at collection time for
their own GPU-availability skip check, and wgpu-native's C-level instance is a
true process-wide singleton that raises if `set_instance_extras` is called
after it already exists; a first attempt at a *lazy*, call-inside-`create()`
version of this fix broke 20 of those tests for exactly that reason -- fixed by
moving the call to module-import time instead, which this file's own "Lesson"
sections exist to capture for next time this class of ordering bug shows up).

## Current status (2026-09-02, launch time re-profiled)

Re-profiled after the graphics-completeness pass above, per the user's mid-session
"launch time is horrendous" flag. **Headline finding: no regression from any recent
code — the architecture-level ~1.1s number from 2026-08-20 still reproduces exactly
(0.535s Window/wgpu bring-up + 0.300s CellRenderer shader compile + the rest ~0.27s,
total 1.110s measured fresh this session) — but that number turns out to be the *best*
case, not the typical one, which is why it read as an honest-but-misleading "not
obviously broken-slow" verdict last time.**

Real, reproduced variance found this session: instrumenting `Window()`'s init in
isolation across several fresh, independent Python processes (not a warm/cached
in-process re-run — each a brand-new interpreter), the *first* GPU-context creation in
a burst of activity costs **~2.0s** just for adapter+device negotiation (4x the normal
0.5s), while every subsequent one shortly after costs the normal ~0.5s — reproduced
twice, independently, in two separate unrelated command batches. Total worst-case
launch measured at **~2.5s**, matching "horrendous" far better than the previously
recorded 1.1s.

Two other concrete things confirmed, neither previously known:
- **puppy renders on this laptop's integrated Intel GPU (Intel(R) Graphics ADL GT2, via
  Vulkan), not the discrete NVIDIA RTX 3050** — `wgpu.gpu.enumerate_adapters_sync()`
  shows the RTX 3050 is only exposed via **OpenGL**, not Vulkan, on this system (the
  same hybrid-GPU-offload quirk already on file for Steam's CEF/ANGLE/zink issue, see
  `project_steam_gpu_rendering_lag.md`). `GpuContext.create()` calls
  `wgpu.gpu.request_adapter_sync(canvas=canvas)` with no `power_preference` at all;
  explicitly passing `power_preference="high-performance"` was tried and does **not**
  change the outcome, since wgpu-native still only sees Intel among Vulkan-capable
  adapters — there's no Vulkan-level path to the NVIDIA GPU here at all, so this isn't
  a one-line fix. wgpu-native's GL backend (the only way to reach the RTX 3050 on this
  box) is the less-mature of its backends and was not tried live — untested, not
  recommended to chase without a real comparison, given real risk of trading a slow
  launch for a broken or *slower* render path.
- **A large (7.2MB), actively-written, shared Mesa on-disk shader cache exists**
  (`~/.cache/mesa_shader_cache/`, populated across many unrelated apps on this system,
  most-recent entries only minutes old at the time of checking) and is the most likely
  explanation for the cold/warm variance — a plausible, not confirmed, root cause:
  puppy's compiled Vulkan shader/pipeline state is one of many entries competing for
  cache residency, and whatever's evicted it (any of this system's many other
  GPU-heavy apps — Steam/games, browsers, Astal's own GPU compositing) between real
  usage sessions would make *every real-world* puppy launch pay the cold cost, not just
  a rare one. Not root-caused further (would need actual driver-level tracing, e.g.
  `MESA_SHADER_CACHE_DISABLE=true` A/B or `perf`, not attempted this session — time
  went to characterizing the variance itself, which was the actually-missing piece
  from the 2026-08-20 report).

**No code changed for this finding** — same honest-report posture as 2026-08-20, but
now with the actual worst-case number and a real, still-unconfirmed lead (shared Mesa
shader-cache eviction) instead of just "one-time GPU bring-up cost." Concrete next
steps, none attempted, no direction picked unilaterally: (a) confirm the shader-cache
theory with a deliberate cold-cache A/B, (b) try wgpu-native's GL backend against the
real NVIDIA adapter as a live experiment (uncertain payoff, real regression risk), (c)
accept this as an environmental cost outside puppy's own code and not chase further.

## Current status (2026-08-31, config file)

**A real config file built — `~/.config/puppy/config.toml`, covering the
first of the three things flagged as "still hardcoded in app.py"
(`font_size`, `font_family`, `theme`; keybinds deliberately not attempted,
see below). 346 tests passing (up from 336), 10 new.**

- **New `src/puppy/config.py`**: `Config` dataclass (`font_size: int`,
  `font_family: str | None`, `theme: str | None`) + `load_config(path)`.
  Uses the stdlib `tomllib` (Python 3.11+, already this project's real
  minimum per `pyproject.toml`) — no new dependency. A missing or malformed
  config file is never fatal, same "fall back cleanly" convention
  `theme.py` already follows for a missing/broken theme: `load_config`
  catches `OSError`/`tomllib.TOMLDecodeError` and returns plain defaults
  rather than raising. Wrong-typed values (e.g. `font_size = "big"`) are
  ignored, not trusted — real, deliberate input validation at this one
  system boundary (a config file a human hand-edits), consistent with the
  project's "only validate at system boundaries" convention.
- **`theme.py`**: `load_theme()` gained a `theme_name` param — when set,
  resolves directly to `~/.config/theme-switcher/themes/<theme_name>/`
  instead of following the wallpaper symlink that otherwise always tracks
  whatever theme is active system-wide. Falls back to the normal
  active-theme resolution if the named directory doesn't exist (a typo'd
  theme name in `config.toml` shouldn't prevent puppy from starting).
- **`app.py`**: `find_monospace_font`/`find_bold_monospace_font` gained a
  `family` param (defaults to `"monospace"`, unchanged behavior) so
  `config.toml`'s `font_family` can point `fc-match` at a specific font
  name instead of the generic alias. `run()`'s `pixel_size` param default
  changed from a hardcoded `16` to `None` specifically so "the caller
  didn't ask for a particular size" is distinguishable from "the caller
  asked for puppy's original 16px" — an explicit caller-supplied
  `pixel_size` still wins outright over `config.toml` (e.g. programmatic
  use), but the common case (just running `puppy` with no arguments) now
  actually lets `config.toml`'s `font_size` take effect.
- **Not built this session** (real, deliberate v1 cut, explicitly flagged
  as much bigger scope, not an oversight): **keybind configuration.**
  Would need changes in both `keyboard.py`'s legacy xterm-style encoder and
  `kitty_keyboard.py`'s CSI-u protocol encoder — a materially larger
  change than font/theme overrides (which only ever needed to thread one
  new optional param through 2-3 already-existing functions). Also not
  built: any config-file *validation/reporting* beyond silent fallback
  (e.g. a warning printed to stderr on a malformed file) — a real, minor,
  low-priority gap, not attempted.
- **Verified**: full suite green (346 passed, 10 new — `test_config.py`
  covering missing/malformed/partial/wrong-typed/empty-string config
  files, `test_render_theme.py` `theme_name` override + not-found-fallback
  + no-override-unchanged cases); a fresh `timeout 4 python -m
  puppy.render.app` live run still starts and shows up correctly in `niri
  msg windows` with no crash/traceback, tested **both** with no config
  file present (the common case) and with a real `font_size = 24`
  `config.toml` present (removed again afterward — not left behind as a
  permanent user-facing config the user didn't ask to create). Not yet
  visually confirmed that a larger `font_size` actually renders bigger
  glyphs live (would need a human watching the window).

## Current status (2026-08-31, double/triple-click select)

**Double-click word-select and triple-click line-select built — the smallest
of the candidates offered after the three daily-driver basics were done
(picked as a direct, low-risk extension of the already-tested selection
model, no new architecture). 336 tests passing (up from 327), 9 new.**

- **`Screen`**: `select_word(row, col)` (confined to one row -- words don't
  span line wraps, matching how `selected_text()`/`cell_selected()` are
  already main-screen-grid-only in scope) and `select_line(row)` (the full
  row, `selected_text()`'s existing per-line rstrip trims trailing blanks
  same as always). Word-character set is kitty's real
  `select_by_word_characters` default, `@-./_~?&=%+#`, confirmed against
  kitty's `options/definition.py`, plus anything Python's `str.isalnum()`
  agrees is alphanumeric (matching kitty's own documented rule: its literal
  set "in addition to" real Unicode alphanumerics). Clicking on a
  non-word character (whitespace, or anything outside that set)
  deliberately selects nothing, rather than guessing at kitty's separate,
  more complex whitespace-run convention.
- **`InputState`**: a left press within `_CLICK_INTERVAL` (0.5s, kitty's
  real `click_interval` fallback default) of the previous one, on the same
  cell, bumps a 1→2→3→1… click counter; 2 calls `select_word`, 3 calls
  `select_line`. New injectable `clock: Callable[[], float] | None = None`
  constructor param (defaults to `time.monotonic`, same no-op-default-
  injection pattern as `copy_to_clipboard`/`Screen.write_back`) lets tests
  drive click timing deterministically instead of racing real sleeps.
  Same-cell (not pixel-distance) tolerance is a deliberate v1
  simplification -- cheap to reason about since positions are already
  cell-quantized.
- **Not built this session** (real, deliberate v1 cut): after a word/line
  click, dragging (without releasing) falls through to ordinary per-cell
  extension from that word/line's boundary, not kitty's own word-wise/
  line-wise drag-extend behavior. The initial multi-click selection itself
  is correct; extending it by dragging isn't multi-click-aware. Not
  attempted -- would need a persistent "selection mode" (char/word/line)
  threaded through the whole drag, a real bigger change than this pass's
  scope.
- **Verified**: full suite green (336 passed, 9 new — `test_screen.py`
  word/line-select model tests incl. punctuation-set and whitespace-click
  cases, `test_render_input_state.py` click-count timing/cell-reset tests
  via the new fake-clock fixture); a fresh `timeout 4 python -m
  puppy.render.app` live run still starts and shows up correctly in `niri
  msg windows` with no crash/traceback. Not yet interactively confirmed
  with a real double/triple click.

## Current status (2026-08-31, scrollback view)

**Scrollback UI (mouse-wheel-into-history) built — the last of the three
"daily-driver basics" items flagged back on 2026-08-24 (cursor, selection,
scrollback view — all three now done). 326 tests passing (up from 312), 14
new. Built immediately after text selection in the same session/direction,
without re-asking, since it was the one remaining item on an already-user-
approved list.**

- **`Screen`** (`screen.py`): `scroll_offset` (int, 0 = live grid, the
  overwhelmingly common state) + `scroll_view(lines)` (positive = up into
  history, clamped to `[0, len(scrollback)]`) + `reset_scroll_view()` +
  `scrolled_back` property + `visible_rows()` (returns exactly `self.rows`
  rows — the live grid unchanged at offset 0, or a window into
  `list(scrollback) + grid` otherwise; combined length is always exactly
  `len(scrollback) + rows`, so the slice is always in-bounds for any
  clamped offset, no top-padding logic needed). `visible_rows()` re-clamps
  `scroll_offset` against the *current* scrollback length at read time (not
  just whatever it was when last set), so `CSI 3 J` clearing scrollback out
  from under an active scroll can never produce a negative/out-of-range
  slice. New `in_alt_screen` property (`self._alt_active` was already
  tracked, just had no public accessor). `scroll_view()` itself is
  deliberately *not* alt-screen-aware — same design as `start_selection`
  not being mode-gated — the real gate lives in `InputState`, matching
  `mouse_reporting_active`'s existing role for selection. `resize()` and
  both `enter_alt_screen()`/`exit_alt_screen()` now also call
  `reset_scroll_view()` (alongside the `clear_selection()` they already
  called) — a resize changes what a given offset would even show, and the
  alt-screen grid about to be shown has nothing to do with scroll_offset's
  meaning.
- **`InputState`** (`input_state.py`): `on_scroll` now branches exactly like
  the selection press does — `screen.mouse_reporting_active` (plus a new
  `screen.in_alt_screen` check, since vim/less/htop already scroll
  themselves and forwarding *and* locally scrolling would be double
  behavior) decides whether a wheel event moves puppy's own viewport
  (`screen.scroll_view`) or gets reported to the program as a real
  SCROLL_UP/SCROLL_DOWN mouse event, unchanged from before. Scroll amount
  per notch: `abs(yoffset) * 5` lines, rounded, minimum 1 — the `5` is
  kitty's own real `wheel_scroll_multiplier` default (confirmed against
  kitty's `options/definition.py`), not invented. **Real, documented gap**:
  kitty additionally branches on high-precision scrolling devices
  (trackpads, and wheel mice specifically on Wayland/macOS), scrolling
  proportional to the raw pixel delta instead of this flat per-notch
  amount — GLFW's `yoffset` doesn't expose that distinction here, and
  puppy doesn't attempt to infer it, so every device scrolls at the same
  flat rate regardless of precision. `on_key` now also calls
  `screen.reset_scroll_view()` on every real press (alongside the
  selection-clearing it already did) — typing while looking at history
  snaps back to the live bottom and still sends the keystroke normally,
  matching real terminals. No Shift override for wheel scroll (unlike
  selection's Shift-forces-local rule) — real terminals don't have one for
  this; Shift-scroll answers to a different real convention (fast/inverse
  scroll) that puppy doesn't implement, not simulated here.
- **Rendering** (`app.py`'s `build_instances()`): the per-cell loop now
  iterates `screen.visible_rows()` instead of `screen.grid` directly.
  `viewing_live = not screen.scrolled_back` gates both the cursor
  (`cursor_shape` forced to `"none"` while scrolled back) and selection
  (`show_selection` forced `False`) — both are main-screen-grid concepts
  whose stored coordinates would point at the wrong visual cells once
  `visible_rows()` is showing scrollback content at those same row
  indices, matching real terminals (which hide the cursor and any
  selection while you're looking at history).
- **Real interaction bug found and fixed while writing this, not shipped as
  a gap**: selection coordinates are always interpreted against the live
  grid (`Screen.cell_selected`/`selected_text` never consult
  `visible_rows()`), so starting a drag while `scrolled_back` would have
  silently selected/copied whatever's really at those row/col positions in
  the *live* grid — not the scrollback content actually on screen, which
  also never renders as selected either (see `build_instances`'s
  `viewing_live` gate above). Fixed in `InputState.on_mouse_button`: a left
  press only starts a local selection when `not screen.scrolled_back`, in
  addition to the existing mouse-reporting/shift check — otherwise it falls
  through to the normal (harmless no-op, since no mouse mode is active
  either) report path. Caught by re-reading the two features together
  before calling either done, not by a live report.
- **Not built this session** (real, deliberate v1 cuts): no visual
  indicator that you're scrolled back at all (real terminals often dim the
  view, show a "history" badge, or similar — puppy just silently shows
  different content, easy to lose track of); no keyboard scrollback
  navigation (Shift+PageUp/PageDown, common in many terminals); no
  precision-aware wheel-scroll amount (see above); selecting text into
  scrollback itself (as opposed to *while looking at* scrollback, fixed
  above) isn't supported at all — that's a real, larger feature (selection
  coordinates would need to address scrollback+grid combined, not just the
  live grid), deliberately deferred, not attempted this pass.
- **Verified**: full suite green (326 passed, 14 new — `test_screen.py`
  scroll-view model tests including a real deliberately-shrunk-scrollback
  no-crash test, `test_render_input_state.py` local-vs-reported and
  typing-resets tests, `test_render_app.py` scrolled-back-content and
  cursor/selection-suppression tests); a fresh `timeout 4 python -m
  puppy.render.app` live run still starts and shows up correctly in `niri
  msg windows` (`app_id: "puppy"`) with no crash/traceback. **Not yet
  interactively confirmed** — same caveat as text selection, needs a human
  actually spinning a real mouse wheel in a live window with real
  scrollback content built up first.

## Current status (2026-08-31, text selection)

**Text selection (click-drag, copy) built — the user's explicit pick when
asked which "daily-driver basics" item to do next (the other options offered
were scrollback UI, kitty-graphics completeness, or just visually confirming
the already-built cursor). 312 tests passing (up from 292), 20 new. No GPU/
shader changes needed at all — selection highlighting reuses the exact same
"just recolor this cell's fg/bg before upload" technique the block cursor
already established, not a new rendering path.**

- **`Screen`** (`screen.py`): `selection_start`/`selection_end` ((row, col)
  drag anchors, main-screen grid only — v1 deliberately doesn't select into
  scrollback, since there's no scrollback UI to select *from* yet, see the
  still-open item below) + `start_selection()`/`update_selection()`/
  `clear_selection()`/`has_selection()`/`cell_selected(row, col)`/
  `selected_text()`. `has_selection()` is deliberately False for a same-cell
  click with no drag (`start == end`) — matches every real terminal: a plain
  click positions/deselects, it doesn't select the one cell under the
  pointer. `cell_selected()` is inclusive of both the start and end cell
  (the cell under the pointer during a drag reads as selected, matching real
  visual feedback), normalized via a new `_selection_bounds()` helper so
  callers never reason about drag direction (dragging right-to-left or
  bottom-to-top just works). `selected_text()` rstrips each line, matching
  `dump_text()`/`scrollback_text()`'s existing convention. New
  `mouse_reporting_active` property (`1006 in private_modes and bool(
  private_modes & {1000, 1002, 1003})`) — same generic-private-mode-set
  pattern `bracketed_paste`/`focus_tracking`/`sync_output_pending` already
  use. Selection is cleared on `resize()` (stale coordinates would point
  outside the new grid), and on both `enter_alt_screen()`/`exit_alt_screen()`
  (a vim/less/htop taking over the grid has nothing left for a main-screen
  selection to point at — real terminals deselect on this same transition).
- **`InputState`** (`input_state.py`): a left-button press decides once,
  at press time, whether this drag is a local selection or gets forwarded to
  the program as a mouse report — `screen.mouse_reporting_active` gates it,
  *unless* Shift is held, which always forces local selection regardless
  (real xterm/kitty convention, confirmed against kitty's own docs, not
  assumed). A press that starts a selection never also emits a mouse report
  for the same press/release, matching how a real terminal doesn't
  double-deliver a click it's handling itself. `on_cursor_pos` calls
  `screen.update_selection()` instead of reporting DRAG while `self.
  selecting` is true. `on_key` clears any active selection on every real
  key press (not repeat/release) — typing over a stale selection would be
  actively misleading, not just cosmetic. New constructor param
  `copy_to_clipboard: Callable[[str], None] | None = None` (no-op default —
  same injection pattern as `Screen.write_back`), called once on release
  with `screen.selected_text()` if `has_selection()` is true.
- **`theme.py`**: `Theme.selection_fg`/`selection_bg`, parsed from
  `kitty-colors.conf`'s real `selection_foreground`/`selection_background`
  keys (confirmed present in both `midnight2` and `focusedpanic`'s real
  files). Falls back to kitty's own real stock defaults (`#000000`/
  `#fffacd`, confirmed against kitty's `options/definition.py`) when a theme
  doesn't set them, rather than inventing new placeholder values.
- **Rendering** (`app.py`'s `build_instances()`): a selected cell's fg/bg is
  overridden to the selection colors as a flat replacement (not a blend),
  matching kitty's real `selection_foreground`/`selection_background`
  semantics — computed after any `cell.reverse` swap so selection always
  wins over reverse video, but *before* the cursor-cell check, so a block
  cursor sitting on a selected cell still shows cursor colors, not selection
  colors (matches real terminal behavior: the cursor is drawn on top of
  everything). `show_selection = screen.has_selection()` is computed once
  per frame outside the per-cell loop, not per-cell — on the overwhelmingly
  common case of no active selection this keeps the loop's cost identical to
  before the feature existed.
- **New `render/clipboard.py`**: `copy_selection(window_handle, text)` copies
  to two independent, both best-effort targets — CLIPBOARD via GLFW's own
  cross-platform `glfwSetClipboardString` (portable off this machine, what a
  real Ctrl+V paste in most apps reads from), and PRIMARY via `wl-copy -p`
  (Wayland-specific, silently skipped if the binary is missing — matches the
  X11/Wayland convention every other terminal on this system follows: a
  drag-selection is immediately available for middle-click paste, no
  explicit copy needed). GLFW has no PRIMARY-selection concept at all
  (Windows/macOS don't have one), so that half can't go through the portable
  path. New `Window.copy_to_clipboard(text)` wraps it, keeping the GLFW
  handle encapsulated in `Window` like every other GLFW-specific operation;
  `app.py`'s `run()` wires `InputState`'s `copy_to_clipboard` param to it.
- **Not built this session** (real, deliberate v1 cuts): no double-click
  word-select or triple-click line-select (kitty defaults both have);
  no rectangle/block selection (kitty's Ctrl+Alt-modified select); no
  visual scroll-while-dragging-past-the-edge; no middle-click paste (PRIMARY
  is written so paste-elsewhere works, but puppy itself doesn't read it back
  in); selecting is confined to the visible main-screen grid only, matching
  the still-missing scrollback UI (see Next steps).
- **Verified**: full suite green (312 passed, 20 new — `test_screen.py`
  selection-model tests, `test_render_input_state.py` click/drag/shift/
  release-copies tests, `test_render_app.py` selection-color and
  cursor-wins-over-selection tests, `test_render_theme.py` selection-color
  parsing + fallback tests); a fresh `timeout 5 python -m puppy.render.app`
  live run still starts and runs the full 5 seconds with no crash/traceback
  (only the pre-existing harmless `libdecor-gtk-WARNING`); `niri msg
  windows` confirms the window still opens correctly (`app_id: "puppy"`).
  **Not yet interactively confirmed** — no real mouse-drag has been driven
  into the live window from here (would need `ydotool`/similar pointer-
  simulation tooling, not attempted, and real click-drag-release + paste-
  elsewhere is the kind of thing worth the user trying directly rather than
  simulating blind). Next session, if this is still open: have the user
  actually drag-select some text in a live `puppy` window, confirm it
  highlights in the theme's real selection colors, and confirm a paste
  elsewhere (Ctrl+V into another app) produces the right text.

## Current status (2026-08-31, cursor)

**Visible text cursor built — the user picked this explicitly from the
"daily-driver basics" list the 2026-08-24 session left open, over extending
the kitty graphics protocol further.** Full DECSCUSR/DECTCEM support, all
three real shapes, real blinking, all theme-driven. 292 tests passing (up
from 273), including 3 new real GPU pixel-readback proofs of the shader
logic. Committed and pushed this session.

- **`Screen`**: `cursor_visible` (bool, defaults `True`) + `set_cursor_visible()`
  for DECTCEM (`CSI ?25h/l`); `cursor_shape` (`"block"`/`"underline"`/`"beam"`/
  `"none"`) + `cursor_blink` (bool) + `set_cursor_shape()` for DECSCUSR
  (`CSI Ps SP q`), mode-to-shape/blink mapping confirmed exact against kitty's
  real `screen_set_cursor` (`screen.c`) — odd modes blink, even modes are
  steady, mode 0 resets to the default blinking block, mode 7+ means no
  cursor shape at all (distinct from DECTCEM's own visibility toggle).
- **`Parser`**: mode 25 is special-cased in `_dispatch_private_mode` (like
  alt-screen) rather than folded into the generic `private_modes` set, since
  its real default is *visible* — the opposite of every other tracked mode's
  default-off. New minimal intermediate-byte tracking (`_intermediate`,
  0x20-0x2F bytes, capped at 4 chars) recognizes DECSCUSR's single space
  intermediate (`CSI Ps SP q`) as distinct from a bare `CSI Ps q` — not a
  general intermediate-byte engine, just enough for this one real sequence.
- **`theme.py`**: `Theme.cursor`/`cursor_text_color`, parsed from
  `kitty-colors.conf`'s real `cursor`/`cursor_text_color` keys (confirmed
  present in every real theme checked), falling back to `fg`/`bg` when a
  theme doesn't set them.
- **Rendering** (`cell_renderer.py` + `app.py`): a block cursor needs no
  shader support at all — `build_instances()` just swaps that one cell's
  fg/bg to `cursor_text_color`/`cursor_color` before upload, so the character
  underneath still renders, now in contrasting colors, no extra draw call or
  instance. Underline/beam shapes *do* need shader support (a bar decoration
  that doesn't touch the glyph or the cell's real fg/bg): the `Instance`
  struct grew a `cursor: vec4<f32>` field (rgb = bar color, w = shape code:
  0=none/1=underline/2=beam) and the fragment shader draws a thin bar at the
  cell's bottom or left edge (reusing the existing `underline_thickness`
  uniform for the bar width rather than adding a new one) — independent of,
  and drawn after, the real text-underline band, so a cursor can sit on an
  underlined cell without conflict. `draw_frame()` computes real 1Hz blink
  timing (`int(time.time() * 2) % 2 == 0`, gated by `cursor_blink` and
  `cursor_visible`) and passes it into `build_instances()`'s new
  `show_cursor`/`cursor_color`/`cursor_text_color` params — which default to
  "off"/plain-fg/plain-bg, so every pre-existing caller/test that doesn't
  care about the cursor renders exactly as before.
- **Instance struct grew, all existing tuple-constructed test instances
  updated** (8 sites in `test_render_cell_renderer.py` + `app.py`'s own
  `build_instances`) to append the new `cursor` field — same kind of
  mechanical-but-real update the underline flag's addition needed earlier.
- **Real bug found and fixed while testing this, not before shipping it**:
  `Screen.put_char` legitimately lets `cursor_col` reach exactly `screen.cols`
  right after filling the last column (a deferred-wrap state — the actual
  wrap only happens on the *next* write, matching real terminal behavior).
  `build_instances()` was comparing against the raw `screen.cursor_row/col`,
  so the cursor briefly vanished (matched no real cell) every time it sat in
  that state — e.g. typing into a narrow/full terminal. Fixed by clamping to
  `rows-1`/`cols-1` for rendering purposes only, matching how every other
  terminal keeps the cursor visually glued to the last column instead of
  disappearing off the edge; `Screen`'s own wrap-pending state is untouched.
  Caught by a test (`cols=1`, immediately after `put_char`) before it ever
  reached a live window, not found by eyeballing.
- **Live smoke-tested, not yet visually confirmed with real contrast.**
  `timeout 5/8 python -m puppy.render.app` starts and runs clean, no crash/
  traceback, and `niri msg windows` confirms the window opens correctly. But
  the *currently active* RengeOS theme (`focusedpanic`) sets `cursor #000000`
  against a `#0c151e` background (`(12, 21, 30)`) — barely distinguishable
  from the background even when rendering exactly correctly, confirmed
  directly via `load_theme()` rather than assumed. **This is the exact same
  root-cause category as the already-diagnosed cat-art/`unifetch-colors.conf`
  issue from 2026-08-24** (a theme content choice, not a puppy rendering
  bug) — flagged directly to the user mid-session when they asked "why are
  the colors still weird" about an old test screenshot, and confirmed by
  them to be that already-known cat-art issue, unrelated to the cursor work.
  A theme with genuine cursor/background contrast was found for a real
  future visual proof but not yet used: `vim-substrata`
  (`cursor #f0ecfe` on `background #191c25`) — **next session, if a live
  visual confirmation of the cursor is still wanted, switch to that theme
  first** (or pass a `Theme(...)` with contrasting colors directly to
  `run()`/`build_instances()` for a one-off check) rather than relying on
  `focusedpanic`'s own near-black cursor color, which will always look faint
  or invisible here regardless of whether the code is correct.
- **Not built this session** (real, deliberate scope cuts, not oversights):
  no focus-based shape difference (kitty dims/hollows the cursor when the
  window loses focus — puppy always renders as if focused); no
  `cursor_text_color`-vs-fg HSLuv contrast override (kitty has one, puppy
  just uses the theme's literal `cursor_text_color`); multi-cursor (kitty's
  `extra_cursors`) not implemented, single cursor only, matching this
  project's scope so far.

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
- [x] **Second real live user bug report, acted on same session (2026-08-24).** User
      compared puppy directly against xfce4-terminal/OdyTTY, flagged 4 things. Full
      diagnosis + fixes in the 2026-08-24 Current status entry above and the Next
      steps recap (kept there rather than duplicated here) — summary: (1) DA1
      (`CSI c`)/DA2 (`CSI > c`) device-attribute queries were never answered at all,
      causing a real ~10s fish/tish startup hang + degraded-mode warning, fixed via
      `Screen.report_primary/secondary_device_attributes()` + a new `>`-prefix branch
      in `Parser`; (2) no blur, root-caused to puppy's window never setting a Wayland
      app-id so RengeOS's existing per-window blur system had nothing to match,
      fixed via `glfw.window_hint_string(glfw.WAYLAND_APP_ID, "puppy")` in
      `window.py` (real gotcha: has to be set *after* calling `glfw.init()` ourselves
      first, since `rendercanvas.glfw`'s own `glfw.init()` call resets hints on a
      real first init); (3) cat ascii-art body reading as solid black, partially
      addressed by switching bold rendering to a real bold `.ttf` face (`fc-match
      "monospace:bold"`) instead of relying only on synthetic
      `FT_GlyphSlot_Embolden` — real, tested, measured improvement, but a rigorous
      controlled A/B (identical byte stream, real GPU readback, only font config
      changed) showed it's a small (~2%) contributor; the dominant cause is the
      active theme's own `unifetch-colors.conf` hardcoding literal `#000000` for
      part of the art, confirmed near-invisible against this theme in xfce4-terminal
      too (a real cross-terminal pixel-sampled comparison, not assumed) — that's a
      RengeOS theme-file fix, out of this repo's scope, flagged not actioned; (4)
      launch speed not re-investigated, no new information. 14 new tests (6
      `test_render_font.py` real-bold-face tests, 3 `test_screen.py` DA1/DA2, 5
      `test_parser.py` DA1/DA2 end-to-end through real escape bytes), full suite
      green (273 passed). All 3 real fixes live-confirmed via the grim/niri
      screenshot technique this same session (DA1: clean prompt past the old 10s
      cutoff; blur: `niri msg windows` reports `app_id: "puppy"`; bold: real bold
      face verified loaded and producing measurably less ink than synthetic).
      **Open question for the next session, not yet answered**: whether to shift
      priority to daily-driver basics that are currently missing entirely (no
      visible text cursor, no text selection, no scrollback UI/mouse-wheel-into-
      history) rather than continuing kitty-graphics-protocol completeness — see
      Next steps below, ask the user rather than picking unilaterally.
- [x] **Visible text cursor — 2026-08-31.** Block/underline/beam shapes
      (DECSCUSR, `CSI Ps SP q`), DECTCEM show/hide (`CSI ?25h/l`), real 1Hz
      blink, all colors theme-driven (`kitty-colors.conf`'s `cursor`/
      `cursor_text_color` keys). See the Current status entry dated
      2026-08-31 for full implementation detail (`Screen`/`Parser`/`theme.py`/
      `cell_renderer.py`/`app.py` changes, the real deferred-wrap clamp bug
      found and fixed while testing this, and why live visual confirmation
      needs a theme switch first). 19 new tests (292 total, up from 273),
      including 3 real GPU pixel-readback proofs of the underline/beam bar
      shader logic. Live smoke-tested (no crash/traceback), not yet visually
      confirmed with real contrast — the currently active theme's own cursor
      color is itself near-black against its background, unrelated to this
      code's correctness. **Root cause of the theme's own black cursor now
      diagnosed** (not a puppy bug, a RengeOS theme-switcher one, and not
      specific to this one theme — 564 of 566 themes have the same literal
      `#000000` cursor regardless of background): full writeup at
      `~/Documents/theme-switcher-cursor-black-bug-report.md`, not fixed
      here, out of this repo's scope.
- [x] **Text selection (click-drag, copy) — 2026-08-31.** `Screen.
      start_selection`/`update_selection`/`clear_selection`/`has_selection`/
      `cell_selected`/`selected_text`, `mouse_reporting_active` property
      gating local-selection-vs-forwarded-mouse-report (Shift always forces
      local selection). Highlighting reuses the block cursor's plain
      fg/bg-recolor technique, no shader changes. Copies to both CLIPBOARD
      (GLFW, portable) and PRIMARY (`wl-copy -p`, best-effort) via new
      `render/clipboard.py`. See the "Current status (2026-08-31, text
      selection)" entry above for full detail. 20 new tests (312 total, up
      from 292). Live smoke-tested (no crash, window opens correctly), not
      yet interactively confirmed with a real mouse drag.
- [x] **Scrollback UI (mouse-wheel-into-history) — 2026-08-31.** `Screen.
      scroll_view`/`reset_scroll_view`/`scrolled_back`/`visible_rows`,
      `in_alt_screen` property. `InputState.on_scroll` splits local-vs-
      reported exactly like selection's press handler does
      (`mouse_reporting_active` + a new `in_alt_screen` check), 5 lines per
      wheel notch (kitty's real `wheel_scroll_multiplier` default). Typing
      snaps back to the live bottom. Cursor/selection are suppressed while
      scrolled back (both are live-grid concepts). Real bug found and fixed
      in the same pass, not shipped as a gap: a drag started while scrolled
      back would have silently selected/copied live-grid content instead of
      what's on screen — now blocked in `InputState.on_mouse_button`. See
      the "Current status (2026-08-31, scrollback view)" entry above for
      full detail. 14 new tests + 1 regression test (327 total, up from
      312). Live smoke-tested (no crash, window opens correctly), not yet
      interactively confirmed with a real mouse wheel. This completes all
      three "daily-driver basics" items flagged on 2026-08-24 (cursor,
      selection, scrollback view).
- [x] **Double-click word-select / triple-click line-select — 2026-08-31.**
      `Screen.select_word`/`select_line`, word-character set matching
      kitty's real `select_by_word_characters` default. `InputState` click-
      counting via a new injectable `clock` param (`_CLICK_INTERVAL` 0.5s,
      kitty's real default). See the "Current status (2026-08-31,
      double/triple-click select)" entry above for full detail. 9 new tests
      (336 total, up from 327). Live smoke-tested (no crash, window opens
      correctly), not yet interactively confirmed. Real, documented v1 cut:
      dragging after a word/line click isn't multi-click-aware (falls back
      to ordinary per-cell extension).
- [x] **Config file (`~/.config/puppy/config.toml`) — 2026-08-31.**
      `font_size`/`font_family`/`theme` overrides, new `src/puppy/config.py`
      (stdlib `tomllib`, no new dependency). `theme.py`'s `load_theme()`
      gained a `theme_name` override param; `app.py`'s
      `find_monospace_font`/`find_bold_monospace_font` gained a `family`
      param. See the "Current status (2026-08-31, config file)" entry above
      for full detail. 10 new tests (346 total, up from 336). Live
      smoke-tested both with and without a real config file present, no
      crash. Real, deliberate v1 cut, explicitly bigger scope: keybind
      configuration (would need changes in both `keyboard.py` and
      `kitty_keyboard.py`, not attempted here).

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
    config.py                  Config/load_config() — ~/.config/puppy/config.toml (font_size/font_family/theme)
    render/
      __init__.py          toolkit-choice rationale pointer
      gpu.py                 GpuContext — canvas-agnostic adapter/device/surface + clear()
      color.py                sRGB<->linear conversion (GPU wants linear, themes are sRGB)
      window.py                live GLFW window + raw input-callback registration
      clipboard.py               copy_selection() -- CLIPBOARD via GLFW + PRIMARY via wl-copy
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
    test_config.py           config.toml parsing, real temp-dir fixtures, no live-machine dependency
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

**Nothing is mid-flight; there is no unfinished code to pick back up.** The 2026-09-02
animation pass (see the "Current status (2026-09-02, kitty graphics animation: a=f/a=a)"
entry above for full detail) is real, committed, pushed, test-covered (394 passing, up
from 372), and confirmed via both real GPU pixel-readback tests and a direct
`Parser`->`Screen`->`GraphicsManager` integration smoke test — but not yet visually
confirmed in a live window (a real multi-frame animation playing correctly on screen is
still unverified by an actual human). **Launch time is now closed, pending a new
concrete lead**: the NVIDIA-wake bug is genuinely fixed (`puppy/render/gpu.py` restricts
wgpu-native's Instance to non-GL backends), the shared-Mesa-shader-cache-eviction theory
was tested and refuted, and the window now paints its real background immediately after
creation instead of sitting undefined through `CellRenderer`'s ~0.3s shader-compile step
(all three as of 2026-09-03 — see the two entries above). Total *actual* cold launch is
still an honest ~1.1-1.5s with no further actionable lever short of a much bigger
architecture change; recommend not spending more time here unless something new turns
up. Eight things total are now real-but-only-smoke/unit-tested, not yet
interactively/visually confirmed by a human: the visible cursor (needs a theme with real
cursor/bg contrast — see the cursor entry for which one), text selection, scrollback
view, double/triple-click, config.toml's `font_size` actually resizing glyphs live, the
`a=p`/`a=d`/`a=q`/z-index/cropping graphics work, this pass's animation playback, and
(implicitly, always) every launch-time number itself, which is all
synthetic-script-measured — the one live launch this session (used to confirm the early
paint above) was deliberately brief and terminated by the test itself, not a real
day-to-day session.

**`a=c` (compose two already-existing frames) was explicitly scoped out of this
pass** — a real, separate follow-up if animation work continues (see the Current
status entry for why it doesn't fold into `a=f`). Other real candidates, none picked
unilaterally: keybind configuration (the one config-file piece deliberately deferred —
see the config-file entry for why), tabs/splits, Sixel, or the interactive confirmation
pass mentioned above.

### 2026-08-24 session recap (second live user bug-report pass)

User compared puppy directly against xfce4-terminal/OdyTTY and flagged 4 things.
3 were real puppy bugs, now fixed and live-confirmed (see the Current status entry
dated 2026-08-24 for full detail, and the Milestones entries below):
- **Fish/tish DA1 10s-timeout warning** — puppy never answered `CSI c`/`CSI > c` at
  all. Fixed via `Screen.report_primary/secondary_device_attributes()` + a new `>`
  prefix in `Parser`.
- **No blur (unlike OdyTTY)** — puppy's window had no Wayland app-id, so RengeOS's
  existing per-window blur system (`Mod+Ctrl+A`, app-id-keyed niri window-rules) had
  nothing to target. Fixed in `window.py` — real gotcha: `rendercanvas.glfw`'s
  `glfw.init()` resets hints on first real init, so the app-id hint has to be set
  after calling `glfw.init()` ourselves first, not just before constructing
  `RenderCanvas`.
- **Cat ascii-art body reads as solid black, clashes with the theme** — bold now uses
  a real bold `.ttf` face (`fc-match "monospace:bold"`) instead of relying only on
  synthetic `FT_GlyphSlot_Embolden`. **This is a real, tested, worthwhile improvement,
  but it is NOT the main fix for what the user is seeing** — a rigorous controlled A/B
  (identical byte stream, real GPU pixel readback, only the font config changed)
  showed it closes only ~2% of the density gap. The actual dominant cause: the active
  theme's own `~/.config/theme-switcher/themes/midnight2/unifetch-colors.conf`
  hardcodes literal `#000000` (real 24-bit truecolor) for part of the cat outline,
  which renders near-invisible against this theme's `#000029` background in *any*
  spec-compliant truecolor terminal — confirmed by capturing and pixel-sampling
  xfce4-terminal rendering the exact same content, not assumed. **This is a RengeOS
  theme-file fix, not a puppy code fix** — if the user wants it addressed, it means
  editing that theme's `unifetch-colors.conf` (or `apply-unifetch-theme.py`'s
  generation logic if it's wrong for every theme, not just this one), a different
  repo/scope than puppy itself. Not done this session — flagged, not actioned,
  since it's outside this project.
- **Launch still feels slow** — not re-investigated this pass, no new information
  beyond the existing ~1.1s/wgpu-bring-up-dominated measurement from 2026-08-20.

**The "daily-driver basics" list from 2026-08-24 (cursor, selection,
scrollback view) is now fully done** — see the "All three..." paragraph
above for the current candidate list to pick from next. Still real, still
missing, lower priority than any of the above: double-click word-select /
triple-click line-select (a small extension of the selection model already
built, not a new subsystem), ligatures/complex-script shaping, selecting
*into* scrollback itself (as opposed to *while looking at* it, which now
works) — see the scrollback-view Current status entry's "real interaction
bug found and fixed" note for why that's a deliberately deferred, larger
feature, not an oversight.

Separately, low-effort/high-value if there's a spare cycle: switch the active
RengeOS theme to `vim-substrata` (real cursor/bg contrast, `#f0ecfe` on `#191c25`
— see 2026-08-31 entry) and take one real live screenshot of the cursor blinking
in a window, the one piece of this feature that's tested and smoke-tested but not
yet actually seen.

Separately, whenever there's a spare cycle, unrelated to any of the above: live-test
   `TERM=puppy python -m puppy` (the *text* pass-through harness, `__main__.py`) in a
   real terminal window (a real ncurses/vim session using the terminfo entry, not just
   `curses.setupterm()` accepting it headlessly) — still pending since it was first
   noted, needs an interactive session.

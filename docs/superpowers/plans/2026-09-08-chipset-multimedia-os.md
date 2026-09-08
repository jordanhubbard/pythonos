# Chipset Multimedia OS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:executing-plans (inline; user said "make it" / "continue").
> Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship a HAL-free Python chipset (copper, playfields, sprites,
blitter, Paula, raster) that owns GUI presents, plus Workbench-as-View,
a sprite demo, and a Video Toaster View.

**Architecture:** One active View per tick. Raster composites PF0, keyed
PF1, and 8 sprites into an XRGB/BGRX backbuffer and presents once.
Paula mixes 4 looping channels into the existing mixer. Compositor
paints Workbench playfields only while the chipset clock runs.

**Tech Stack:** CPython 3.14 frozen into PythonOS; asyncio FrameClock;
existing `Framebuffer.present` / pythonos_bridge `surface.upload`;
host-side `python3 tests/chipset_test.py` (no pytest, no QEMU).

**Spec:**
`docs/superpowers/specs/2026-09-08-chipset-multimedia-os-design.md`

## Global Constraints

- No `_hal` / `kernel.hal.io` imports in `kernel/chipset/` at module
  level.
- Pixel buffer layout matches `kernel.display.framebuffer.Surface`:
  BGRX little-endian (`B,G,R,0xFF` per pixel). Color ints are
  `0x00RRGGBB`.
- Do not add pytest. Follow `tests/smoke_test.py` `check()` style.
- Do not commit unless the user explicitly asks (user git rule).
- `make test-chipset` must pass on the host without Docker/QEMU.
- Serial `make run` path unchanged.
- Freeze is automatic for `kernel/**/*.py`.

---

### Task 1: Raster chipset (HAL-free)

**Files:**
- Create: `kernel/chipset/__init__.py`
- Create: `kernel/chipset/clock.py`
- Create: `kernel/chipset/copper.py`
- Create: `kernel/chipset/playfield.py`
- Create: `kernel/chipset/sprite.py`
- Create: `kernel/chipset/blitter.py`
- Create: `kernel/chipset/raster.py`
- Create: `kernel/chipset/view.py`
- Create: `tests/chipset_test.py`
- Modify: `GNUMakefile` (add `test-chipset`, hook into `test-x86_64`
  and `test-arm64` as a host-first step)
- Modify: `Makefile` `TOP_GOALS` to include `test-chipset`

**Interfaces:**
- Consumes: nothing from later tasks
- Produces:
  - `MODE_DIRECT = "direct"`, `MODE_INDEXED = "indexed"`
  - `Wait(line: int)`, `Move(reg: str, value: int)`
  - `Playfield(width, height, mode=MODE_DIRECT)` with `.pixels`
    (bytearray), `.scroll_x`, `.scroll_y`, `.put(x,y,color)`,
    `.get(x,y) -> int`
  - `Sprite` with `.place(pixels, w, h, x, y, key_color=0)`,
    `.enabled`
  - `View(width, height, mode=MODE_DIRECT, scale=1)` with `.pf0`,
    `.pf1`, `.sprites` (len 8), `.copper`, `.palette` (list 32),
    `.bplcon`, `.key_color`, `.diw_start`, `.diw_stop`
  - `blitter.fill/copy/cookie(...)`
  - `chipset.load_view(view)`, `chipset.tick() -> bytes` (dest
    backbuffer), `chipset.set_present(cb)`, `chipset.set_dest(w,h)`
  - Dest default 1024×768

- [x] **Step 1: Write the failing host test**

Create `tests/chipset_test.py` with a `check(name, cond, detail="")`
helper, `sys.path` insert of repo root, and tests for: copper MOVE
at Wait line changes COLOR00; blitter clip; cookie-cut; 8×8 indexed
raster golden; sprite key; `load_view(None)` raises ValueError.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 tests/chipset_test.py`

Expected: FAIL (cannot import `kernel.chipset`)

- [x] **Step 3: Implement kernel/chipset modules**

Pure Python. Raster: for each dest row, map to playfield Y via
integer scale and letterbox; run copper; composite PF0, keyed PF1,
sprites. INDEXED uses palette. DIRECT uses XRGB stored as BGRX
bytes. `tick()` writes dest buffer, calls present callback if set,
returns dest bytes.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 tests/chipset_test.py`

Expected: all checks PASS, exit 0

- [x] **Step 5: Makefile**

Add `test-chipset:` running `python3 tests/chipset_test.py`.
Prefix `test-x86_64` and `test-arm64` with it so `make test` fails
fast. Add `test-chipset` to `Makefile` `TOP_GOALS`.

---

### Task 2: Paula

**Files:**
- Create: `kernel/chipset/paula.py`
- Modify: `kernel/chipset/clock.py` / `__init__.py` to mix Paula on
  tick and call injected mixer
- Modify: `tests/chipset_test.py`

**Interfaces:**
- Consumes: `chipset.tick()` from Task 1
- Produces:
  - `Paula` with `.channel[0..3]` each having `sample`, `rate`,
    `volume` (0–64), `pan` (0–255), `loop`, `loop_start`,
    `loop_end`, `playing`, `play()`, `stop()`
  - `Paula.mix(frames: int) -> bytes` int16 stereo 48000 Hz
  - `chipset.set_mixer(obj)` where obj has `play_pcm(...)`

- [x] **Step 1: Failing tests**

Two channels, different pan, looping short square wave, mix 1600
frames: output length `1600*4`, not all zeros, left vs right
energy differs. No mixer attached: mix still works. Volume 0:
silence.

- [x] **Step 2: Run — expect FAIL** (`Paula` missing)

- [x] **Step 3: Implement paula.py + tick integration**

On tick, `mix(48000 // 30)` and `mixer.play_pcm` if set.

- [x] **Step 4: Run `python3 tests/chipset_test.py` — PASS**

---

### Task 3: Workbench View + present handoff

**Files:**
- Modify: `kernel/gui/compositor.py`
- Modify: `kernel/commands.py` `pythonos_gui`
- Modify: `kernel/chipset/__init__.py` (ensure_workbench helper)
- Modify: `docs/gui.md` (Chipset section)
- Modify: `README.md` (short GUI Mode note)

**Interfaces:**
- Consumes: `chipset.load_view`, `chipset.tick`, `chipset.start`,
  `chipset.set_present`, `chipset.set_mixer`
- Produces: Workbench View 1024×768 DIRECT; compositor compose
  without `fb.present` when chipset is running; present callback
  uses `fb.present` or one bridge upload

- [x] **Step 1: `pythonos_gui` starts chipset clock, LoadView
  workbench, `set_mixer(mixer)`, `set_present(...)`, then
  `compositor.start()`.**

- [x] **Step 2: Compositor `_redraw`: if `chipset.is_running` and
  active view is workbench, paint into workbench PF0/PF1 (local
  compose, guest-backed window bodies) and call `chipset.tick()`.
  If active view is not workbench, skip compose (game/toaster own
  the scan). If chipset is not running, keep today's present
  paths.**

- [x] **Step 3: Present callback**

Local: `fb.present(buf)`. Bridge: upload dest buffer to the
window surface handle then `display.present`. Force guest-backed
window surfaces when chipset running so bodies blit.

- [x] **Step 4: Paint a minimal local dock/menubar onto PF1 when
  chipset owns display (bitmap font), so Workbench is not an empty
  navy field. Reuse existing chrome paint.**

- [ ] **Step 5: QEMU `make test-gui-x86_64` after a kernel rebuild.
  Refresh desktop goldens if tile hashes shift for honest
  Workbench-as-View pixels. Do not inflate `max_diffs`.**

---

### Task 4: Showcases (sprites + toaster)

**Files:**
- Create: `kernel/chipset/toaster.py` (wipe helper: animate
  `diw_stop` or a reveal column)
- Create: `apps/demos/sprites.py`
- Create: `apps/toaster/__init__.py`
- Create: `apps/toaster/toaster.py`
- Modify: `apps/demos/__init__.py`
- Modify: `kernel/commands.py` to import `apps.toaster`
- Modify: `apps/_icons.py` if a factory is required
- Modify: `tests/chipset_test.py` (wipe helper)
- Modify: `tests/gui_smoke_test.py` (import kernel.chipset over
  REPL)

**Interfaces:**
- Consumes: View, chipset.load_view, blitter, paula, Wait/Move
- Produces: registry names `sprites` and `toaster`; ESC loads
  workbench

- [x] **Step 1: Host test for `toaster.wipe_step(view, t, duration)`
  moving DIW or key reveal.**

- [x] **Step 2: Sprite demo — 320×200 INDEXED scale 3, copper
  COLOR00 bands, player sprite, Paula loop, arrows/space/ESC.**

- [x] **Step 3: Toaster — 640×400 DIRECT, PF0/PF1 patterns, W wipe,
  1/2 select, Paula stinger, ESC.**

- [x] **Step 4: Register both; `pythonos_gui sprites` /
  `pythonos_gui toaster`.**

- [ ] **Step 5: GUI smoke REPL: `import kernel.chipset as c; c.chipset`
  succeeds.**

---

## Spec coverage

| Spec requirement | Task |
|---|---|
| Copper WAIT/MOVE, unknown reg logs | 1 |
| Dual playfields DIRECT/INDEXED | 1 |
| 8 sprites + key | 1 |
| Blitter fill/copy/cookie clip | 1 |
| Raster letterbox scale, 30 Hz tick | 1 |
| Host `tests/chipset_test.py` | 1–2, 4 |
| Paula 4-ch mix, silent without backend | 2 |
| Workbench View, compositor does not present | 3 |
| Bridge as sink | 3 |
| Sprite demo View | 4 |
| Toaster View + wipe | 4 |
| REPL import | 1, 4 |
| `make test-chipset` | 1 |
| No `_hal` in chipset | 1–2, 4 |

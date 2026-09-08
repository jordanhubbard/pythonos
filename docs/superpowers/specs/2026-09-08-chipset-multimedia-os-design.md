# PythonOS Software Chipset — Design

Date: 2026-09-08
Status: approved in conversation (architecture, components, data
flow). This file is the written spec for the first sub-project.

## Problem

PythonOS already has a stacking desktop, a PySDL2-shaped API, image
decoders, and a one-shot PCM mixer. That is a workstation overlay, not
a game/multimedia machine. An Amiga 2500 / Atari-class OS lets authors
poke a chipset from the shell: copper, playfields, sprites, blitter,
and Paula. The Video Toaster is a client of those chips, not a second
engine.

Python is the kernel and the shell. The chips are pure Python.

## Goal

Ship a guest-side software chipset that owns every GUI framebuffer
present, plus two Views that prove it:

1. A sprite demo (game identity).
2. A Video Toaster studio (multimedia identity).

Both, and the Workbench desktop, are `LoadView()` targets. The serial
`make run` path is unchanged.

## Non-goals (v0)

- GPU drivers, real genlock, or video capture hardware.
- Cycle-accurate 68000 copper timing, HAM, or AGA.
- Tracker disk formats (MOD/XM) beyond a tiny built-in sample bank.
- USB, IME, clipboard.
- Replacing the sdl2 compatibility package. sdl2 apps keep working as
  Workbench window clients.
- Changing `make run` / serial REPL.

## Architecture

A **View** is copper + two playfields + eight sprites + a 32-color
palette + a pixel mode. Exactly one View is active. Each simulated
vblank, the raster walks that View into a guest XRGB8888 backbuffer
and presents once.

```
  FrameClock (asyncio, 30 Hz; tests call tick())
        │
        ▼
  Copper  → register file (palette, key, priority, display window)
        │
        ▼
  Raster  → PF0, keyed PF1, then sprites → backbuffer
        │
        ├── local: Framebuffer.present(buf)
        └── bridge: one full-frame upload + display.present
        │
  Paula   → mix 4 looping channels → mixer.play_pcm (48 kHz int16 stereo)
```

Workbench, the sprite demo, and Toaster are Views. `LoadView(view)`
swaps them at the next tick. ESC on a showcase View loads Workbench
again.

The compositor no longer calls `fb.present`. It paints Workbench
playfield bitmaps. The chipset is the only present path while the
clock is running.

## Components

### FrameClock (`kernel.chipset.clock`)

- Default 30 Hz to match today's compositor `_tick_hz`.
- Kernel: `start(loop)` spawns an asyncio task.
- Tests: `tick()` runs one frame with no event loop and no `_hal`.
- `await chipset.vblank()` waits for the next tick (asyncio.Event).
- If a frame overruns the period: skip the next frame, log once per
  overrun burst.

### Copper (`kernel.chipset.copper`)

Not cycle-accurate. Instructions execute as the raster's playfield Y
advances.

- `Wait(line)` — hold until playfield Y >= line.
- `Move(reg, value)` — poke the register file.
- Unknown register: log, continue (do not raise mid-raster).
- Copper Y is playfield space, not framebuffer space after scaling.

v0 register file:

| Name | Meaning |
|---|---|
| `COLOR00`–`COLOR31` | Palette (INDEXED views) |
| `BPLCON` | Bit0 = PF1 key enable; bits 1–2 = PF priority |
| `KEY_COLOR` | Chroma/index key for PF1 |
| `DIWSTART` / `DIWSTOP` | Inclusive Y window in playfield lines |

No `COPJMP` in v0.

### Playfields (`kernel.chipset.playfield`)

Two playfields per View: `pf0` (back) and `pf1` (front).

- `MODE_DIRECT` — XRGB8888 bytes (Workbench, Toaster).
- `MODE_INDEXED` — one byte per pixel, 0–31, looked up in palette
  (games). Copper `COLOR*` moves are visible on INDEXED views only.

Each playfield has `scroll_x`, `scroll_y` (wrap) so games can scroll
without a blitter copy every frame.

PF1 may be chroma/index keyed using `KEY_COLOR` when the key bit in
`BPLCON` is set. Keyed pixels show PF0.

### Sprites (`kernel.chipset.sprite`)

Eight slots. Each sprite has `x`, `y`, `w`, `h`, `enabled`,
`key_color`, and a pixel buffer in the View's mode. Drawn after
playfields. Transparent where the pixel equals `key_color` (DIRECT) or
index 0 (INDEXED, unless `key_color` is set).

No attached sprite pairs in v0.

### Blitter (`kernel.chipset.blitter`)

Software, playfield pixel space, clips to dest bounds (no exception):

- `fill(dest, x, y, w, h, color)`
- `copy(src, dest, sx, sy, dx, dy, w, h)`
- `cookie(src, mask, dest, sx, sy, dx, dy, w, h)` —
  cookie-cut: where mask is non-key, write src; else leave dest.

`src` / `dest` / `mask` are Playfield objects (or any object with
`width`, `height`, `mode`, `pixels` bytearray).

### Paula (`kernel.chipset.paula`)

Four channels. Each channel:

- `sample: bytes` — int16 little-endian mono
- `rate: int` — Hz (default 22050)
- `volume: int` — 0–64
- `pan: int` — 0 (left) … 128 (centre) … 255 (right)
- `loop: bool`, `loop_start`, `loop_end` — sample indices
- `playing: bool`

`paula.mix(frames) -> bytes` produces int16 stereo at 48000 Hz. The
raster/clock pushes that buffer through `mixer.play_pcm` when a mixer
backend is attached. With no backend: mix still runs (tests), present
to hardware is a silent no-op.

Paula does not import `_hal`. Inject a mixer-like object with
`play_pcm(samples, channels=2, fmt="int16")`.

### Raster (`kernel.chipset.raster`)

For each playfield row Y in the active View:

1. Advance copper until all `Wait`s for this Y are satisfied; apply
   `Move`s.
2. Composite PF0 then keyed PF1 then sprites into a row of XRGB.
3. Integer-scale the View into the dest backbuffer (letterbox if the
   dest is larger). Default dest is 1024×768 to match the framebuffer.

`present` is a callback set by the kernel (`fb.present` or one bridge
upload). Tests capture the backbuffer without presenting.

### View (`kernel.chipset.view`)

```python
View(width, height, mode=MODE_DIRECT, scale=1)
# .pf0 .pf1 .sprites[8] .copper .palette[32]
# .bplcon .key_color .diw_start .diw_stop
```

`chipset.load_view(view)` raises `ValueError` if `view` is None.
The new View is used on the next `tick()`.

## Workbench as a View

- Size 1024×768, `MODE_DIRECT`, scale 1.
- `pf0` = desktop background (existing PNG or solid).
- `pf1` = windows + dock + menubar, keyed on a reserved color
  (`KEY_COLOR = 0x000000` with PF1 cleared to that color before paint,
  or a dedicated key `0xFF00FF` unused by chrome).

When the chipset clock is running:

1. Compositor compose uses the **local** path into Workbench
   playfields. Window surfaces must be **guest-backed** so bodies
   blit. Host-blit-only Workbench is not used while the chipset owns
   display.
2. Compositor does **not** call `fb.present`.
3. Chipset `tick()` rasters and presents once.
4. If pythonos_bridge is up, present is one `surface.upload` of the
   backbuffer plus `display.present`. The bridge is a sink, not the
   compositor.

Existing sdl2 apps keep working: they draw into `CompositorWindow`
surfaces that the Workbench paint copies onto PF1.

`pythonos_gui` starts the chipset clock, `LoadView(workbench)`, then
`compositor.start()`.

## Showcases

### Sprite demo (`apps/demos/sprites.py`)

- `LoadView` a 320×200 `MODE_INDEXED` View, scale 3 (960×600, letterboxed
  on 1024×768).
- PF0 starfield or scrolled color bands (copper `COLOR00` per Wait).
- Player sprite + a few enemy sprites.
- Blitter `cookie` or `copy` for a missile.
- Paula: looping bass on ch0, one-shot on ch1 for a shot.
- Arrows move, space fires, ESC `LoadView(workbench)`.

### Video Toaster (`apps/toaster/toaster.py`)

- `LoadView` a 640×400 `MODE_DIRECT` View, scale 1, letterboxed.
- PF0 = "Program A" (animated plasma or color bars).
- PF1 = "Program B" (different pattern), keyed / wiped.
- Wipe: animate `DIWSTART`/`DIWSTOP` or a horizontal key reveal over
  ~30 frames. Dissolve: blend is **out of v0**; wipe + chroma key only.
- Title as a sprite overlay.
- Paula stinger (short sample) when the wipe starts.
- Keys 1/2 select A/B, W starts wipe, ESC returns to Workbench.

Dock / Demos menu launches both. They are fullscreen Views, not
nested windows — matching Amiga screens. The earlier "Toaster window"
wording means "launched from the desktop", not "a compositor window".

## REPL surface

Frozen with the kernel, importable at `>>>`:

```python
from kernel.chipset import (
    chipset, View, Playfield, Sprite,
    MODE_DIRECT, MODE_INDEXED,
    Wait, Move, blitter, paula,
)

v = View(320, 200, mode=MODE_INDEXED, scale=3)
v.palette[0] = 0x102040
v.copper.instructions = [Wait(0), Move("COLOR00", 0x102040),
                         Wait(100), Move("COLOR00", 0x401010)]
v.sprites[0].place(art, x=40, y=80)
chipset.load_view(v)
```

`chipset` is a process-wide singleton, same idea as `compositor` and
`mixer`.

## Error handling

| Case | Behavior |
|---|---|
| `load_view(None)` | `ValueError` |
| Unknown copper MOVE | log, skip, raster continues |
| Blitter out of bounds | clip |
| Paula, no mixer backend | mix in software; no hardware write |
| Raster overrun | skip a frame, log |
| `tick()` with no active View | fill dest with black, still present |
| Present callback missing | keep backbuffer; tests read it |

No generic `except Exception: pass` on the raster path.

## Testing

Host-side, no QEMU, no `_hal`. Chipset packages must not import
`_hal` or `kernel.hal.io` at module level.

Existing GUI tests are QEMU scripts (`tests/smoke_test.py` style), not
pytest. Do **not** add a second runner. Add:

- `tests/chipset_test.py` — host script, repo root on `sys.path`,
  `check()` helpers matching other tests. Covers copper, blitter clip,
  cookie-cut, indexed raster golden (tiny 32×24 View), sprite key,
  Paula mix of two tones (non-silence, stereo pan), `load_view(None)`
  raises, wipe helper moves DIW.

Makefile: `test-chipset` runs that script. `make test` runs it **before**
QEMU smokes so chipset regressions fail fast.

QEMU: extend `tests/gui_smoke_test.py` (or a thin sibling) to
`import kernel.chipset` and `chipset.tick()` over the TCP REPL, assert a
serial/REPL marker. Desktop goldens will change because present
ownership changes; refresh
`tests/goldens/x86_64/desktop.tilehashes` with
`PYTHONOS_GOLDEN_REFRESH=1` once Workbench-as-View looks right.
Do not weaken `max_diffs` to hide a broken raster.

Audio: Paula uses `mixer.play_pcm`; existing `tests/audio_smoke_test.py`
stays. Optional: a REPL snippet that plays a Paula loop; not required
for v0 if unit tests cover mix.

Coverage target for new `kernel/chipset/` code: at least 70% of
statements exercised by `tests/chipset_test.py`.

## Module layout

`tools/freeze_kernel.py` already freezes every `kernel/**/*.py`.
Apps freeze the same way they do today (imported from `pythonos_gui`).

```
kernel/chipset/__init__.py   # singleton, LoadView, MODE_*, re-exports
kernel/chipset/clock.py
kernel/chipset/copper.py
kernel/chipset/playfield.py
kernel/chipset/sprite.py
kernel/chipset/blitter.py
kernel/chipset/paula.py
kernel/chipset/raster.py
kernel/chipset/view.py
kernel/chipset/toaster.py    # wipe/key helpers used by the Toaster app
apps/demos/sprites.py
apps/toaster/__init__.py
apps/toaster/toaster.py
tests/chipset_test.py
docs/gui.md                  # add Chipset section
```

No new C. No new host SDL ops except reusing existing
`surface.upload` / `display.present` for the one-frame sink.

## Integration points (existing files)

- `kernel/commands.py` `pythonos_gui`: start chipset clock + Workbench
  View before `compositor.start()`.
- `kernel/gui/compositor.py`: compose into Workbench playfields; stop
  presenting when chipset is running; force guest-backed window
  surfaces.
- `apps/registry.py` / `apps/demos/__init__.py` / dock: register
  `sprites` and `toaster`.
- `docs/gui.md`: document chipset + `LoadView`.
- `README.md`: one short subsection under GUI Mode. Provenance chapter
  only if the README origin story is updated (see `skills/PROVENANCE.md`).

## Success criteria

- From `>>>`: `import kernel.chipset` works. A 10-line copper list
  changes background color by scanline.
- `pythonos_gui sprites` takes over the display with sprites + Paula;
  ESC returns to Workbench.
- `pythonos_gui toaster` shows A/B layers and a wipe; ESC returns.
- Workbench dock, menubar, and existing apps still function.
- `make test-chipset` passes on the host without QEMU.
- `make test` / `make test-gui` pass after golden refresh.

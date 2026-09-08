# Development Plan: pythonos (main branch)

*Generated on 2026-09-08 by Vibe Feature MCP*
*Workflow: [qrspi](https://codemcp.github.io/workflows/workflows/qrspi)*

## Goal

Turn PythonOS from a serial-REPL OS with an optional SDL-shaped desktop
into a **game and multimedia workstation**: Amiga/Atari-class audio,
video, and graphics, with Python as both kernel and shell. Authors poke
a software chipset (`>>>` is the control panel); the OS supplies copper,
blitter, sprites, Paula-style audio, and a Video Toaster compositor
entirely in Python on top of the existing framebuffer and mixer.

## Key Decisions

- **Task tracking (2026-09-08):** mac project `pythonos` is the
  authoritative ledger (`mac task`). Open beads were imported as mac
  tasks and then closed in beads. Do not use `bd` for new work.
- **Dock (2026-09-08, approved):** Default dock is `category="app"`
  including toaster. Unpinned running apps appear while running and
  leave when the launch ends and no windows remain. Keep in Dock /
  Remove from Dock on dock-icon context-click (session-only pins).
  Desktop wallpaper right-click / two-finger / control-click shows
  Demos and Games. Arcade titles are `category="game"`. System → Games
  was added so those titles stay reachable from the menu bar.
  Tracking: `task_f9e1a49b91e740b4847f8618c1ea5254`.
- Serial `make run` is unchanged. The desktop continues to exist, but
  as the Workbench **View** sitting on the chipset, not as a second
  painter of the framebuffer.
- Guest remains CPU-only (QEMU virt). No GPU drivers. The "chips" are
  pure-Python raster and audio engines writing the same XRGB8888 fb
  and int16-stereo mixer already in the kernel.
- **API personality (2026-09-08):** software chipset. Game and media
  authors poke Python devices (copper, blitter, sprites, Paula). The
  Video Toaster is a client of those chips, not a separate engine.
  High-level pygame-shaped APIs are out of v0 except as thin examples.
- **First showcase (2026-09-08):** both. Chipset is usable from the
  REPL, plus a sprite demo *and* a Video Toaster window. Proves games
  and multimedia on the same devices.
- **Display ownership (2026-09-08):** chipset **is** the display.
  The rasterizer owns the framebuffer every vblank. The existing
  compositor becomes the Workbench view: windows, dock, and menubar
  are playfield (and optionally sprite) clients, not a competing
  painter. Games and Toaster swap Views (copper + playfields +
  sprites) instead of fighting the desktop for `fb.pixels`.
- Existing `make run` serial REPL is unchanged. `make run-gui` boots
  into Workbench-as-playfield. Existing sdl2 apps keep working if
  their surfaces blit onto the Workbench playfield.
- **Architecture (2026-09-08, approved):** software Agnus/Denise/Paula
  owns the framebuffer every vblank. A View is copper + two playfields
  + sprites + palette. Workbench, sprite demo, and Toaster are Views.
  Serial `make run` is unchanged.
- **Components (2026-09-08, approved):** FrameClock, Copper WAIT/MOVE,
  dual playfields (DIRECT 32-bit for Workbench/Toaster, INDEXED for
  games), 8 sprites, Blitter copy/fill/cookie-cut, Paula 4-channel
  looping into the existing mixer, Raster.
- **Data flow / present (2026-09-08, approved):** raster is the source
  of truth. One present per frame (`fb.present` or one bridge upload).
  FrameClock 30 Hz. Workbench compositor paints playfields only.
- **Arcade cabinet (2026-09-08):** three LoadView demos on the same
  chips — `defender` (scroll + dual PF), `pacmaze` (INDEXED maze +
  ghosts), `raiders` (formation + dive). Rules live in
  `apps/arcade_logic.py` (host-tested). ESC returns to Workbench.

## Notes

### Research facts (2026-09-08)

- Framebuffer is XRGB8888 1024×768. `Framebuffer.present(buf)` bulk-flushes
  a BGRX back buffer via `_hal.mmio_write_buf32`.
- Compositor runs at 30 Hz (`_tick_hz = 30`) on asyncio, not a display
  interrupt. PIT/`_pit_ticks` is 100 Hz. There is no vblank IRQ.
- `make run-gui` prefers **pythonos_bridge**: host libSDL2 owns pixels;
  guest issues `surface.fill_rect` / `surface.blit` / `display.present`.
  Local `fb.present` is the fallback when the bridge is down.
  Many `SDL_Surface` objects are host-backed (`pixels is None`).
- Mixer is a singleton. `play_pcm` converts to int16 stereo 48 kHz and
  calls `backend.write_pcm`. No concurrent mix; a second play replaces
  or queues at the device. No looping, period, or per-channel volume.
- Backends: Intel HDA (x86) and virtio-snd (arm64). `write_pcm` is
  single-shot per call.
- `tools/freeze_kernel.py` freezes every `kernel/**/*.py` automatically.
  New `kernel/chipset/` modules are included without a freeze-list edit.
- Desktop goldens (`tests/goldens/x86_64/desktop.tilehashes`) hash the
  `pythonos_gui bouncing_ball` screen. Changing who paints the fb will
  require golden refresh and gui/desktop smoke updates.
- Historical beads memory `desktop-os-scope`: guest is CPU-only; GPU deferred.
  Historical beads memory `gui-architecture-mirror-sdl`: long-term host SDL via
  `sdl.call`. Chipset-as-display is a **guest** raster; it does not
  replace that memory for sdl2 apps, but Workbench present must not
  fight the raster for the same pixels.

Present path (resolved 2026-09-08): raster is source of truth; one
present per frame (local `fb.present` or one bridge upload). Bridge is
a sink, not the compositor, while the chipset clock is running.

## Questions

<!-- beads-phase-id: pythonos-aom.1 mac-task-id: task_d24ec3064917bb769b44580b2c22bc80 -->

**Entrance criteria:** user request understood; existing GUI/audio
mapped; the one architectural fork (chipset vs high-level runtime)
is asked.

**Exit criteria:** API personality chosen; first sub-project named;
out-of-scope list agreed.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Research

<!-- beads-phase-id: pythonos-aom.2 mac-task-id: task_ce38516f1419ac3dcbe215f42c7933d2 -->

**Entrance criteria:** Questions exit criteria met.

**Exit criteria:** Amiga/Atari/Toaster metaphors mapped onto current
PythonOS modules; constraints (30 fps software blit, 48 kHz mixer)
documented.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Design

<!-- beads-phase-id: pythonos-aom.3 mac-task-id: task_10a499eac9fdce631f2371e36572d0ee -->

**Entrance criteria:** Research complete; personality locked.

**Exit criteria:** Chipset Python API, frame clock, and showcase app
specified in `docs/superpowers/specs/`.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Structure

<!-- beads-phase-id: pythonos-aom.4 mac-task-id: task_2d98f0ef96f210693b5debc711d2352f -->

**Entrance criteria:** Design approved.

**Exit criteria:** Module layout (`kernel/chipset/`, apps) and freeze
list decided.

### Vertical slices

1. **Raster chipset** — View, copper, playfields, sprites, blitter,
   FrameClock.tick, raster backbuffer. Host `tests/chipset_test.py`.
   User-visible on host: a 32×24 golden frame. Files under
   `kernel/chipset/` except paula/toaster.
2. **Paula** — 4-channel mix to int16 stereo; optional mixer sink.
   Host tests for pan/loop/silence. File `kernel/chipset/paula.py`.
3. **Workbench View** — compositor paints playfields; chipset presents
   (local or one bridge upload). `pythonos_gui` starts the clock.
   Existing apps still open. QEMU gui/desktop smokes + golden refresh.
4. **Showcases** — `apps/demos/sprites.py` and `apps/toaster/` as
   LoadView screens with ESC back to Workbench. Dock/Demos registration.

Freeze: no list edit; `kernel/**/*.py` is auto-frozen. Apps imported
from `pythonos_gui` as today.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Plan

<!-- beads-phase-id: pythonos-aom.5 mac-task-id: task_a627b008c41baf46649ca6d64383aa9e -->

**Entrance criteria:** Structure approved.

**Exit criteria:** Implementation steps with tests per module.

Written plan:
`docs/superpowers/plans/2026-09-08-chipset-multimedia-os.md`

Four tasks: raster chipset, Paula, Workbench present handoff,
showcases. TDD on host for 1–2 and 4 helpers. No commits unless
the user asks.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Implement

<!-- beads-phase-id: pythonos-aom.6 mac-task-id: task_9d5a9e0da67f463ee7962ee3d4a93534 -->

**Entrance criteria:** Plan approved; tests exist in `tests/`.

**Exit criteria:** Chipset + showcase runnable via `make run-gui`;
coverage >= 70% on new code.

Host `make test-chipset` is the gate that does not need Docker.
Workbench dock/menubar now paint onto the local compose path
(copied into Workbench PF0); window surfaces are guest-backed
while the chipset clock runs. QEMU GUI smokes still need Docker
+ a kernel rebuild; desktop goldens may need refresh after that.
Do not inflate `max_diffs`.

### Tasks

*Tasks managed via `mac task --project pythonos`*

## Commit

<!-- beads-phase-id: pythonos-aom.7 mac-task-id: task_22cc076958f07713c383859e843c7459 -->

**Entrance criteria:** Implement exit criteria met; `make test` green.

**Exit criteria:** Commits exist only after explicit user request
(user git rule). Matching mac tasks closed.

### Tasks

*Tasks managed via `mac task --project pythonos`*

---
*This plan is maintained by the LLM and uses `mac task --project pythonos`
for task management. Beads (`bd`) is frozen legacy history.*

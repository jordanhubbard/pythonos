# PythonOS GUI subsystem

The GUI is an opt-in layer over the bare-metal kernel — the default boot path is unchanged (`make run` / `make test` still go to a serial-only `>>>` prompt with no SDL window). When you opt in via `make run-gui`, the kernel comes up with a framebuffer, a stacking compositor, mouse + keyboard input, and audio output, and the desktop auto-launches with a full app dock.

Everything below is implemented in Python on top of the same `_hal` extension and asyncio scheduler the rest of the kernel uses; there is no libSDL2 inside the guest. The `sdl2` Python package mimics PySDL2's surface so unmodified PySDL2 sample code can be copied in unchanged.

## Launching the GUI

| Command | What it does |
|---|---|
| `make run-gui` | Boot **and** auto-launch the desktop with the full app dock. Host-side `tools/run_gui.py` spawns `pythonos_bridge`, brings up an SDL window, and sends the kickoff command over the TCP REPL once the kernel is up. |
| `make run-gui PYTHONOS_GUI_APP=<name>` | Same, but pre-launch a specific app: `terminal` / `editor` / `files` / `image_viewer` / `sysmon` / `about` / `clock` / `toaster` for full apps; `bouncing_ball` / `audio_tone` / `starfield` / `rainfall` / `plasma` / `paint` / `life` / `sprites` / `defender` / `pacmaze` / `raiders` for demos. |
| `make run-gui-x86_64` / `make run-gui-arm64` | Explicit per-arch forms. |

Inside the compositor:

- `Tab` / `Shift-Tab` cycle focus between windows.
- Click on a window's title bar to drag it; click in the body to focus + raise it.
- `ESC` typically closes the focused app and returns to the REPL.

## Architecture

```
                ┌──────────── REPL (serial, always available) ───────────┐
                │                                                         │
       pythonos_gui          apps/{terminal, editor, files, …}            │
                │                          ▲                              │
                ▼                          │                              │
       kernel.gui.compositor ───── damage rects ──→ kernel.display.fb      │
                │                          ▲                              │
                ▼                          │                              │
   kernel.gui.sdl2 (frozen also as top-level `sdl2`)                      │
                │                          ▲                              │
        ┌───────┴───────┐           ┌──────┴──────┐         ┌───────────┐ │
        ▼               ▼           ▼             ▼         ▼           ▼ │
 kernel.display    kernel.gui.input         kernel.sound.mixer       …    │
 framebuffer       (Event/EventQueue)                                      │
        │               │                       │                          │
        ▼               ▼                       ▼                          │
 bochs / ramfb    PS/2  | virtio-input    HDA (x86) | virtio-snd (arm64)   │
        ▲               ▲                       ▲                          │
        └─── QEMU ──────┴───────────────────────┴── host SDL2 ─────────────┘
```

## Display drivers

| Arch | Backend | Source |
|---|---|---|
| x86_64 | bochs-VBE / std-VGA via Multiboot2 framebuffer tag | `src/boot/fb.c`, `kernel/display/framebuffer.py` |
| arm64 | QEMU `ramfb` (allocated buffer + fw_cfg blob) | `kernel/drivers/display/ramfb.py`, `fwcfg.py` |

Both deliver a 1024×768 XRGB8888 framebuffer to `kernel.display.framebuffer.fb`. The compositor and `sdl2.SDL_UpdateWindowSurface` write into it via `_hal.mmio_write32`.

## Input drivers

| Arch | Keyboard | Mouse / Pointer | Source |
|---|---|---|---|
| x86_64 | PS/2 (IRQ1, scancode set 1) | PS/2 (IRQ12, 3-byte packet) | `kernel/drivers/keyboard.py`, `kernel/drivers/mouse.py` |
| arm64 | virtio-input keyboard | virtio-input tablet (EV_ABS) and mouse (EV_REL) | `kernel/drivers/input/virtio_input.py` |

All four backends translate to a single canonical `kernel.gui.input.Event` and post into `kernel.gui.input.queue` (an asyncio `Queue`). The compositor and `sdl2.SDL_PollEvent` drain that queue.

Modifier tracking (Shift/Ctrl/Alt) is done in the bridge so consumers receive both `KEY_DOWN`/`KEY_UP` events and shift-aware `Event.text`.

## Audio backends + Mixer

| Arch | Backend | Source |
|---|---|---|
| x86_64 | Intel HDA via PCI (BDL DMA, codec verbs) | `kernel/sound/hda.py` |
| arm64 | virtio-snd via virtio-mmio (control + TX queues) | `kernel/drivers/sound/virtio_snd.py` |

`kernel.sound.mixer.Mixer` wraps the active backend and exposes a uniform `play_pcm(samples, channels=2, rate=48000, fmt='int16'|'float32')`. The Mixer normalises arbitrary input shapes to the backend's native int16-stereo @ 48 kHz format. `sdl2.sdlmixer.Mix_PlayChannel` thunks straight through.

## sdl2 — PySDL2-compatible API

The `sdl2` package is frozen as a top-level module so unmodified PySDL2 source `import sdl2` works. The implementation lives at `kernel/gui/sdl2/`. It is **not** a ctypes wrapper around libSDL2 — the guest has no libSDL2 — it is a pure-Python shim that dispatches to the compositor / input / mixer.

Surface implemented:

| Module | Symbols |
|---|---|
| top-level | `SDL_Init` / `SDL_Quit` / `SDL_WasInit`, `SDL_GetTicks`, `SDL_Delay`, all the subsystem flag constants |
| `video` | `SDL_Window`, `SDL_CreateWindow`, `SDL_DestroyWindow`, `SDL_GetWindowSurface`, `SDL_UpdateWindowSurface`, `SDL_SetWindowTitle` |
| `surface` | `SDL_Surface`, `SDL_PixelFormat`, `SDL_Rect`, `SDL_Point`, `SDL_Color`, `SDL_FillRect`, `SDL_BlitSurface`, `SDL_MapRGB`, `SDL_MapRGBA`, `SDL_FreeSurface`, `SDL_LoadBMP`, `draw_char`/`draw_text` (8×16 bitmap font) |
| `render` | `SDL_Renderer`, `SDL_Texture` (alias for SDL_Surface in software mode), `SDL_CreateRenderer`, `SDL_DestroyRenderer`, `SDL_SetRenderDrawColor`, `SDL_RenderClear`, `SDL_RenderFillRect`, `SDL_RenderDrawRect`, `SDL_RenderCopy`, `SDL_RenderPresent`, `SDL_CreateTextureFromSurface`, `SDL_DestroyTexture` |
| `events` | `SDL_Event`, `SDL_PollEvent`, `SDL_WaitEvent`, `SDL_PumpEvents`, `SDL_QUIT` / `SDL_KEYDOWN` / `SDL_KEYUP` / `SDL_MOUSEMOTION` / `SDL_MOUSEBUTTONDOWN` / `SDL_MOUSEBUTTONUP`, `SDLK_*` keysyms, `KMOD_*` modifier masks |
| `sdlmixer` | `Mix_OpenAudio`, `Mix_CloseAudio`, `Mix_LoadWAV`, `Mix_PlayChannel`, `Mix_HaltChannel`, `Mix_FreeChunk`, `MIX_DEFAULT_*` |
| `sdlttf` | `TTF_Init` / `TTF_Quit`, `TTF_OpenFont`, `TTF_CloseFont`, `TTF_RenderText_Blended` / `_Solid`, `TTF_SizeText` (backed by the bundled bitmap font) |

Compatibility is defined by the corpus tests in `examples/sdl_*.py` running unchanged: `sdl_hello.py`, `sdl_renderer.py`, `sdl_text.py`, `sdl_image.py` (PNG), `sdl_jpeg.py`.

What's deliberately not covered (raise `NotImplementedError` or do nothing): GPU-accelerated rendering, threaded audio with multiple channels, font rendering beyond the bundled 8×16 bitmap, anything that pokes ctypes-specific layout.

## Image decoders

`kernel.gui.image.load_bytes(data) -> SDL_Surface` sniffs magic bytes and dispatches:

| Format | Source | Notes |
|---|---|---|
| BMP | `kernel/gui/image/bmp.py` | 24- and 32-bit uncompressed |
| PPM | `kernel/gui/image/ppm.py` | Netpbm P6 binary RGB, 8-bit |
| PNG | `kernel/gui/image/png.py` + `_deflate.py` | Pure-Python RFC 1951 inflate (~250 LOC) + chunk parser, all 5 filter types, color types 0/2/4/6 at depth 8. Indexed (palette) PNGs raise `NotImplementedError`. |
| JPEG | `kernel/gui/image/jpeg.py` | Baseline DCT (SOF0), 8-bit precision, 1- or 3-component, 4:4:4/4:2:2/4:2:0 chroma subsampling, BT.601 YCbCr→RGB. Progressive / arithmetic-coded JPEGs raise `NotImplementedError`. |

The kernel uses `load_bytes` (not file paths) because libc's `open()` returns ENOSYS in the freestanding build. Callers read through the VFS and pass the bytes in.

## Compositor

`kernel.gui.compositor` is a small stacking window manager:

- **`CompositorWindow(title, x, y, w, h, chrome=True)`** — every app instantiates one; the surface field is an `SDL_Surface` the app draws into.
- **`compositor.add_window(win)`** registers the window; **`compositor.start()`** spawns the draw loop (~30 fps) and the input-routing task.
- **Tab / Shift-Tab** cycle focus globally; mouse-button-down on a title bar starts a drag; click in a window's body focuses + raises it.
- Rendering is whole-screen each frame when any window is dirty (a v0 simplification; per-window damage rects are a planned optimisation).

Color theme: desktop background `0x202840`, focused-window chrome `0x224488`, unfocused chrome `0x303030`.

## Apps

Apps live under `apps/` and self-register at import time via `apps.registry`. The `pythonos_gui` REPL command imports every app package and then either lists them or launches the requested one.

| App | Source | Description |
|---|---|---|
| `terminal` | `apps/terminal/term.py` | Embeds `kernel.shell.Shell` in a 640×400 windowed text grid. Cursor blink + ANSI escape consumer + true scrollback (no content loss on overflow). |
| `editor` | `apps/editor/edwin.py` | Drives `kernel.ed.run` line editor in a 720×480 text grid. |
| `files` | `apps/files/browser.py` | Arrow-key file browser with TCP send/recv. |
| `image_viewer` | `apps/image_viewer/viewer.py` | `pythonos_gui image_viewer <path>`; loads BMP / PPM / PNG / JPEG. |
| `sysmon` | `apps/sysmon/sysmon.py` | Live kernel state — uptime, free RAM (with mini history graph), scheduler process list. Refreshes at 2 Hz. |
| `about` | `apps/about/about.py` | "About PythonOS" — version, arch, SMP CPUs, free RAM, project goals. |
| `clock` | `apps/clock/clock.py` | Big-digit uptime clock with bespoke 5×7 pixel font scaled 5×. Reference for "render text without using the bitmap font path". |
| `bouncing_ball` | `apps/demos/bouncing_ball.py` | A 24×24 rect bouncing in a 320×200 window. ESC closes. |
| `audio_tone` | `apps/demos/audio_tone.py` | Plays 0.5 s of 440 Hz square wave through `Mixer.play_pcm`. |
| `starfield` / `rainfall` / `plasma` | `apps/demos/*.py` | Classic graphics demos — point-cloud animations using fill_rect. |
| `paint` | `apps/demos/paint.py` | Mouse-driven painter (1-7 colors, c clears). Doubles as the simplest reference for "input + drawing through the bridge". |
| `life` | `apps/demos/life.py` | Conway's Game of Life on a fixed grid. Click toggles cells, space pauses, r reseeds, c clears. |

Both `terminal` and `editor` reuse the shared `apps._textwin.TextWin` — a text grid + cursor + scroll wrapping a `CompositorWindow`, exposing `write(text)` and `read_char()` callables that match the Shell / Editor constructor contract.

### Adding a new demo

The simplest demo is around 60 lines and exercises every half of the bridge. Use `apps/demos/paint.py` as a template:

1. Open a window: `win = CompositorWindow("Title", x=, y=, w=, h=); compositor.add_window(win)`.
2. Paint to `win.surface` using `SDL_FillRect`, `SDL_BlitSurface`, `surface.draw_text`, or your own bitmap glyph code (see `clock.py`). Set `win.dirty = True` after any change.
3. Receive events by setting an event handler: `win.set_event_handler(callable)`. The handler runs synchronously inside the compositor's input-routing task — keep it cheap, and use the `state` dict pattern to communicate with the main coroutine.
4. The main coroutine awaits `asyncio.sleep(1.0 / fps)` between frames. Bail out when `state["closed"]` or `win._closed`.
5. Register: `registry.register(name=, description=, entry=main, icon_factory=, category=)`. `category="demo"` puts it in the System → Demos menu only; the default `"app"` adds it to the dock.
6. Add a line `from apps.demos import yourdemo` to `apps/demos/__init__.py` so the import-time `register()` fires.

`apps/_icons.py` has `_new_icon`, `_border`, and per-app icon factories — copy and modify one for your dock icon.

## Chipset (Amiga-class display and audio)

`kernel.chipset` is a software Agnus/Denise/Paula. It owns framebuffer
presents while its clock is running. A **View** is copper + two
playfields + eight sprites + a palette. `chipset.load_view(view)` swaps
the active View; Workbench is one View, games and the Video Toaster are
others.

```python
from kernel.chipset import chipset, View, MODE_INDEXED, Wait, Move
v = View(320, 200, mode=MODE_INDEXED, scale=3)
v.copper.instructions = [Wait(0), Move("COLOR00", 0x102040)]
chipset.load_view(v)
```

Demos: `pythonos_gui sprites` (sprites + copper + Paula; arrows move,
space fires), `defender` (scrolling hills + landers), `pacmaze`
(pellets and ghosts), `raiders` (Galaxian-style formation), and
`pythonos_gui toaster` (dual playfields + wipe). ESC
returns to Workbench. While the chipset clock runs, the compositor
paints Workbench playfields (windows, dock, menubar) and does not call
`fb.present` — the raster is the only present path. Host tests:
`make test-chipset` (no QEMU).

See `docs/superpowers/specs/2026-09-08-chipset-multimedia-os-design.md`.

## Tests

| Suite | Covers | At HEAD |
|---|---|---|
| `tests/smoke_test.py` | x86 default boot + TCP REPL (incl. dynamic compile, vfs_import, multi-line def) | 46 PASS |
| `tests/smoke_test_arm64.py` | arm64 default boot + PL011 | 30 PASS |
| `tests/gui_smoke_test.py` | x86 GUI: sdl2 corpus, compositor render, mouse pipeline, pointer round-trip, serial markers | 23 PASS |
| `tests/desktop_smoke_test.py` | x86 end-to-end: `pythonos_gui bouncing_ball` auto-launch + pixel-perfect checks + tile-hash golden | 5 PASS |
| `tests/audio_smoke_test.py` | x86 audio pipeline: `-audiodev wav,id=a`, runs `examples/tone.py`, parses captured WAV | 6 PASS |
| `tests/gui_smoke_test_arm64.py` | arm64 GUI: ramfb + virtio-input + screendump + sendkey | 8 PASS |

Two pieces of test infrastructure are reusable on their own:

- **`tests/qmp_helper.py`** — `QemuMonitor` wraps QEMU's HMP unix socket (`screendump`, `sendkey`, `mouse_move`, `mouse_button`); `parse_ppm` + `sample_pixel` + `color_close` for pixel checks; `tile_hashes` + `golden_check_or_refresh` for tile-hash goldens (`PYTHONOS_GOLDEN_REFRESH=1` to regenerate).
- **`tests/goldens/x86_64/desktop.tilehashes`** — 3072-tile sha256 baseline of the `pythonos_gui bouncing_ball` desktop. Compared with `max_diffs=200` to tolerate the bouncing animation + minor jitter.

## What's not in the GUI subsystem

- No GPU acceleration (everything is software-rendered)
- No copy/paste, multi-monitor, or hardware cursor
- No alpha blending in the compositor (windows are opaque)
- No font subpixel rendering, IME, or USB hotplug
- Indexed (palette) PNGs, progressive JPEGs, and 12-bit precision JPEGs are not decoded
- The arm64 GUI smoke does not yet cover end-to-end mouse round-trip (no TCP REPL on arm64)

These were all explicit deferrals during phased implementation; see
`mac memory list --project pythonos` and closed historical `pythonos-*`
beads (imported into mac tasks) for the rationale.

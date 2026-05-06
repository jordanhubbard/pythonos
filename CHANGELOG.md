# Changelog

All notable changes to PythonOS are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-05-05

### Other
- release.sh: render each changelog section independently


## [0.1.1] - 2026-05-05

### Added
- Add CHANGELOG.md + auto-categorized release entries

### Fixed
- release.sh: fix unbound-variable in EXIT trap after main returns

### Other
- release.sh: stage changelog entry through tempfile (macOS awk)
- release.sh: render_changelog_entry must always return 0

## [0.1.0] - 2026-05-05

First numbered release. PythonOS boots a frozen CPython 3.14 directly on
bare metal — no POSIX layer between the interpreter and the hardware — and
exposes a Python REPL as the primary user surface on x86_64 and aarch64
QEMU `virt`.

### Added

#### Kernel & runtime
- Bare-metal CPython 3.14 boot: x86_64 multiboot2 ISO via GRUB, arm64
  ELF for QEMU `-kernel`. Reaches `>>>` in well under a second on either arch.
- Free-threading (no-GIL) build on x86, default-GIL build on arm64; SMP via
  multiboot2 startup IPI on x86 and PSCI on arm64 (default 2 CPUs).
- Asyncio task scheduler (`kernel.scheduler`) with `spawn` / `ps` / `kill`
  and a 100 Hz timer driving uptime accounting.
- VFS with tmpfs at `/`, ext2 (read-write) at `/home` and `/apps` over
  virtio-blk, plus `/dev`, `/tmp`, `/proc`, `/sys`, `/bin`, `/examples`.
- TCP REPL server on port 5000 — every connection is its own kernel-level
  Shell with a private namespace and shared kernel objects. Multi-session
  by construction.
- Persistent linenoise history at `/home/.repl_history` (survives reboot
  on arm64 ext2; session-scoped on x86 tmpfs fallback).
- Tab completion at the REPL — both filename and Python attribute
  (`vfs.r<TAB>` → `read` / `read_sync` / `readdir`).
- Dynamic `compile()` of arbitrary Python source, including compound
  statements. (See *Fixed* below for what this took.)
- VFS-backed importer (`kernel.vfs_import`): write a `.py` to `/examples`
  or `/home` and `import` it from the REPL — works for any tmpfs-readable
  file.
- In-kernel chat bus (`kernel.net.chatbus`) — every TCP REPL session gets a
  `chat` object: `chat.send("…")` broadcasts to all other sessions,
  `chat.nick(…)` sets a name, `chat.who()` lists the roster. Two
  `nc localhost 5555` connections can hold a real-time conversation.
- Logo turtle graphics (`kernel.turtle`): forward / right / pen_up /
  goto / setheading / color / home / clear etc., backed by a new
  `surface.line` Bresenham primitive in the SDL bridge.

#### GUI subsystem (opt-in via `make run-gui`)
- Stacking compositor with title bars, drag, focus highlight, click-to-raise.
- macOS-style menu bar with PythonOS / Apps / Demos drop-downs and
  app-specific menus when a focused window declares them. Anti-aliased
  text via SDL_ttf in the bridge.
- Custom desktop background image (1024×768 generated PNG with a
  Pythonos wordmark) and a dock with per-app 48×48 icons.
- Mirror-SDL bridge: SDL2-compatible Python API (`sdl2`) inside the guest
  dispatches to a host-side companion process (`tools/pythonos_bridge`)
  over UART or TCP. Surface, video, render, events, sdlmixer, sdlttf,
  sdlimage all wired.
- Image decoders: BMP, PPM, PNG (pure-Python RFC 1951 inflate), JPEG
  (baseline DCT, BT.601 YCbCr→RGB).
- Audio: HDA on x86, virtio-snd on arm64; unified `kernel.sound.mixer.Mixer`
  with `play_pcm(samples, channels=2, rate=48000, fmt='int16'|'float32')`.

#### Built-in apps (dock + System menu)
- `terminal` — Python REPL inside a CompositorWindow, blinking cursor,
  ANSI CSI escape consumer, real scrollback (window contents survive
  overflow via a new `surface.scroll` bridge op).
- `editor` — `kernel.ed.run` line editor in a window with a File menu
  (Open / Save / Save As / Close) and a footer minibuffer.
- `files` — arrow-key file browser with TCP send/recv.
- `image_viewer` — BMP / PPM / PNG / JPEG viewer.
- `sysmon` — live kernel state: uptime, free RAM (with mini history graph),
  scheduler process list at 2 Hz.
- `about` — "About PythonOS" version + system info window, wired into the
  PythonOS menu's "About PythonOS" item.
- `clock` — big-digit uptime clock with a bespoke 5×7 pixel font scaled 5×.

#### Demos (System → Demos)
- `bouncing_ball`, `audio_tone`, `starfield`, `rainfall`, `plasma` — classics.
- `paint` — mouse-driven paint (1-7 picks color, c clears).
- `life` — Conway's Game of Life on a fixed grid; click toggles cells,
  space pauses, r reseeds, c clears.
- `keyboard` — live event-queue visualizer (every KEY_DOWN / MOUSE_MOVE /
  etc. shown as a colored row). Doubles as bring-up debug for new arches.
- `mandelbrot` — interactive Mandelbrot explorer; click to zoom 2× at the
  cursor, right-click to back out, +/- changes iteration depth, c cycles
  three palettes.
- `spirograph` — Logo turtle drawing classic Spirograph patterns
  (rosette, flower, star, polygon).

#### Network stack
- VirtIO-Net driver (PCI on x86, MMIO on arm64) with arp / ip / icmp /
  tcp / udp.
- TCP listen / accept / send / recv with retransmit + backoff; powers
  `repl_server` on port 5000 and `examples/recv_file.py` on 7000.

#### Build, release, and tests
- Cross-compilation Docker image (`tools/Dockerfile`) builds CPython 3.14
  from source for both arches with appropriate cross-toolchains.
- `tools/freeze_kernel.py` produces a single C array of frozen
  `(name, source, code-object)` tuples that the kernel imports at boot.
- Smoke test suite: `tests/smoke_test.py` (x86), `tests/smoke_test_arm64.py`
  (arm64), `tests/gui_smoke_test.py`, `tests/desktop_smoke_test.py`,
  `tests/audio_smoke_test.py`. **At v0.1.0**: x86 46/46, arm64 30/30,
  GUI 23/23, desktop 5/5, audio 6/6 PASS.
- Release automation: `scripts/release.sh` + `make release` /
  `release-major` / `release-minor` / `release-patch` targets, mirroring
  the nanolang flow (clean tree + gh authed + validate gate + push +
  wait for CI green + annotated tag + GitHub release with bootable
  artifacts attached).

### Fixed

- **The strncmp bug.** `src/libc/string.c:strncmp` was reading byte n+1
  to compute its return value. Effect: every keyword-table lookup in the
  PEG parser (`strncmp("True\0", source_at_True, 4)`) returned non-zero,
  because the literal's byte 5 is `\0` while the source's is `,` or `)`.
  That made the parser tokenize *every* Python keyword
  (`def`/`class`/`for`/`if`/`import`/`True`/`None`/...) as a plain
  identifier, breaking compound statements at runtime. Fixed by rewriting
  strncmp to standard glibc semantics. End-to-end consequence: dynamic
  `compile()` works, multi-line `def` at the REPL works, VFS-backed
  imports work.
- Multi-line `def` / `class` at the REPL — `_py_warnings.py` is now
  frozen, so `codeop.compile_command` (used for incomplete-block
  detection) imports cleanly instead of silently failing back to
  "compile every line as complete".
- Persistent REPL history no longer races the ext2 driver under
  concurrent file operations (was spawning `asyncio.ensure_future` per
  line; now awaited inline).
- Terminal scrollback: window contents are preserved on overflow via
  a new `surface.scroll` SDL bridge op (memmove-based, overlap-safe).
  Previously the entire surface was cleared.
- Terminal: ANSI CSI escape sequences are consumed silently instead of
  rendering as garbage glyphs.
- arm64 ramfb: framebuffer comes up via QEMU `fw_cfg` on
  `qemu-system-aarch64 -device ramfb`.
- arm64 GIC: GICv2/GICv3 auto-detect via PIDR2; works under TCG with
  `cortex-a76` + GICv3.
- Dockerfile (`3440a74` follow-up): cross-compiling x86 from an
  arm64-native builder image now works — added the missing
  `libc6-dev-amd64-cross` + `linux-libc-dev-amd64-cross`, and fetched
  `grub-pc-bin` as a passive payload via `dpkg-deb -x` so
  `grub-mkrescue` can produce BIOS-bootable ISOs without dragging the
  whole amd64 dep tree through apt.

### Known issues / deferred

- Apple Silicon HVF + arm64 GICv2 + virtio-mmio: tripped by an open QEMU
  bug (`hvf.c:2181 assert(isv)`). Workaround: stay on TCG (the default
  `make test-arm64` path).
- x86 PCI virtio-blk read_sector hang — tracked separately;
  `/home` falls back to tmpfs on x86.
- Indexed-palette PNGs and progressive JPEGs are not decoded.

[Unreleased]: https://github.com/jordanhubbard/pythonos/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jordanhubbard/pythonos/releases/tag/v0.1.0

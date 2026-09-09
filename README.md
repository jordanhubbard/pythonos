# PythonOS

A bare-metal operating system where CPython 3.14 **is** the kernel — not a program running on an OS, but the OS itself. Python owns the machine from interrupt handlers to the interactive shell. Runs on x86_64 and arm64 (QEMU `virt`).

Boots directly to a `>>>` prompt on the serial console. The interactive prompt is a real Python REPL — define functions and classes, `import` files written to `/examples` or `/home`, recall history with up-arrow (persisted across reboots on the ext2 mount). An opt-in **GUI desktop** with a stacking compositor, PySDL2-compatible Python API, PNG/JPEG decoders, audio mixer, a macOS-style menu bar (PythonOS / Apps / Demos / Games), a polished desktop background, and a dock of bundled apps (terminal, editor, file browser, image viewer, system monitor, about, clock, toaster) is one make target away — see **GUI Mode** below. Demos and games launch from a two-finger / control-click on the wallpaper (or the Demos and Games menus); they appear in the dock only while running, unless you Keep in Dock.

Run `make help` at any time for the top-level target listing.

## Quick Start

### Prerequisites

- Docker (for cross-compilation toolchain)
- QEMU (`brew install qemu` on macOS)
- CPython 3.14 source tree (fetched by the build)

### First-time build (~10 min to cross-compile CPython)

```bash
make docker-build   # build the Docker cross-compilation image once
make                # cross-compile libpython + kernel, produce build/pythonos.iso
```

### Subsequent builds (fast — libpython is cached)

```bash
make                # rebuild kernel + ISO if sources changed
```

### Run

```bash
# Default boot — serial REPL, no GUI window. Picks host arch automatically.
make run

# x86_64 CPU count defaults to 2; override when needed
SMP_CPUS=4 make run

# Experimental x86_64 CPython free-threading build
PYTHONOS_FREE_THREADING=1 make cleanall all

# Explicit per-arch forms when you want to be specific:
make run-x86_64
make run-arm64

# Kill a running instance
make stop
make stop-arm64
```

Use `Ctrl-A X` to exit QEMU.

### GUI Mode

Opt-in graphical desktop with a stacking compositor, macOS-style menu bar (PythonOS / Apps / Demos / Games), a custom desktop background image, mouse + keyboard input, audio output, and bundled dock apps. Right-click (or two-finger / control-click) the wallpaper to launch demos and games. The default `make run` and `make test` paths are unchanged — they still boot serial-only with `-nographic`.

A software **chipset** (`kernel.chipset`) owns the scan while the GUI
clock runs: copper lists, dual playfields, eight sprites, a blitter, and
four Paula audio channels — all pure Python. LoadView games:
`desktop('sprites')`, `desktop('defender')`, `desktop('pacmaze')`,
`desktop('raiders')`, and `desktop('toaster')`. ESC returns to Workbench.
`make test-chipset` runs the host-side chipset, arcade, and dock tests without QEMU.

```bash
# Boot and auto-launch the desktop in one step.
make run-gui
PYTHONOS_GUI_APP=terminal     make run-gui
PYTHONOS_GUI_APP=editor       make run-gui
PYTHONOS_GUI_APP=files        make run-gui
PYTHONOS_GUI_APP=image_viewer make run-gui
PYTHONOS_GUI_APP=audio_tone   make run-gui
PYTHONOS_GUI_APP=sprites      make run-gui
PYTHONOS_GUI_APP=defender     make run-gui
PYTHONOS_GUI_APP=pacmaze      make run-gui
PYTHONOS_GUI_APP=raiders      make run-gui
PYTHONOS_GUI_APP=toaster      make run-gui
```

The same launcher is available inside every PythonOS REPL. `desktop()`
opens the desktop, an optional name launches one bundled program, and
`desktop('help')` lists everything frozen into the interpreter:

```python
>>> desktop()
>>> desktop('pacmaze')
>>> desktop('help')
```

In the `$` sub-shell, use `desktop`, `desktop pacmaze`, or
`desktop --list`. The older `pythonos_gui` command remains as a compatibility
name for the direct framebuffer backend.

Inside the compositor:
- **Tab** / **Shift-Tab** cycles focus between windows.
- Click a window's title bar to drag it; click in the body to focus + raise.
- **ESC** typically closes the focused app and returns to the REPL.

See **`docs/gui.md`** for the full feature reference (compositor, chipset,
sdl2 API surface, image decoders, audio backends, apps).

### Native debug sessions

For kernel, HAL, CPython, interrupt, or networking failures, start an
agent-debug session rather than relying on the guest REPL alone:

```bash
PYTHONOS_DEBUG=1 make run-gui
python3 tools/pythonos_debug.py serial
python3 tools/pythonos_debug.py qmp status
python3 tools/pythonos_debug.py native -- "bt" "info registers"
```

This creates `build/pythonos-debug.json` with loopback-only TCP REPL details,
a native remote endpoint, a QMP control socket, symbol ELF, and a persistent serial
log. `PYTHONOS_DEBUG_PAUSE=1` halts before execution for early-boot debugging.
For example, repeated `net: rx error` warnings can be inspected with
`python3 tools/pythonos_debug.py serial` even if the guest network stack or
REPL is unavailable. See `skills/PYTHONOS_DEBUG.md` for the agent workflow.

The no-GIL build is currently supported on x86_64 QEMU with SMP enabled. It
boots CPython with `PY_GIL_DISABLED=1`, starts AP-backed pthread workers through
`_thread`, and uses a small mmap shim that validates fixed mappings before
touching memory. The support boundary is still intentionally narrow: native
threads are worker dispatches onto initialized APs, not a full scheduler for
arbitrary blocking POSIX workloads.

### Connect to the TCP REPL

Once the kernel is running, a multi-session Python REPL listens on TCP port 5000 inside QEMU. QEMU forwards it to your host:

```bash
# x86_64
nc localhost 5555

# arm64
nc localhost 5556
```

Each connection gets an independent kernel shell with access to all live kernel objects. Multiple sessions can run simultaneously — this is the point: Python is the kernel, and concurrency is `asyncio`, not fork/exec.

```
PythonOS kernel shell
Python 3.14.0a0
Type help or help() for commands, demos, and examples.
Commands: ls ps pwd cd cat cp mv ftp ed sysinfo netstat
Desktop: desktop()  desktop('pacmaze')  desktop('help')
Examples: examples()  run('/examples/hello_kernel.py')
Helpers: sh()  sh('cmd args')  run('/path')  clear()

>>> 1 + 1
2
>>> sysinfo
PythonOS
Scheduler: 3 tasks
cwd: /
>>> ls /bin
.  ..  ls.py  ps.py  pwd.py  cd.py  cat.py  cp.py  mv.py  ftp.py  ed.py  sysinfo.py  netstat.py  desktop.py  examples.py
>>> cd /tmp
>>> pwd
/tmp
>>> sh()
PythonOS shell: help | examples | desktop [APP] | exit
$ sysinfo
PythonOS
Scheduler: 3 tasks
cwd: /tmp
$ ls
.  ..
$ exit
>>> import kernel.log as log; log.info("hello from REPL")
```

### Unix-like shell experience

The kernel shell offers a Unix-like command experience nested inside the Python REPL. The two modes coexist without friction.

#### Bare-word command dispatch

Any bare identifier typed at `>>>` that is not already a Python name is looked up as `/bin/<name>.py` and executed automatically. No `run()`, no quotes, no `.py` extension needed:

```
>>> ls
>>> ls /bin
>>> ps
>>> pwd
>>> cd /tmp
>>> cat /examples/README.txt
>>> ed /tmp/notes.txt
>>> sysinfo
>>> netstat
```

Arguments are split on whitespace and passed to the script as `argv`:

```
>>> cp /bin/sysinfo.py /tmp/my_sysinfo.py
>>> mv /tmp/my_sysinfo.py /tmp/sysinfo_backup.py
>>> ftp get /tmp/host-file.txt
>>> ftp put /tmp/host-file.txt
```

#### Built-in commands

| Command | Description |
|---|---|
| `ls [path]` | List directory (default: current working directory) |
| `ps` | List kernel tasks with PID, name, state |
| `pwd` | Print current working directory |
| `cd [path]` | Change directory (supports `..`, relative paths; default: `/`) |
| `cat FILE [...]` | Print file contents |
| `cp SRC DST` | Copy a file |
| `mv SRC DST` | Move / rename a file |
| `ftp get DST [PORT]` | Receive one TCP file stream into TmpFS |
| `ftp put SRC [HOST] [PORT]` | Send one TmpFS file over TCP |
| `ed [path]` | ed-style line editor |
| `sysinfo` | System overview |
| `netstat` | Network interface status |
| `desktop [APP]` | Open the desktop, optionally launching a bundled app, demo, or game |
| `examples` | List the readable programs frozen into `/examples` |

#### Shell sub-REPL: `sh()`

Calling `sh()` with no arguments drops into an interactive shell with a `$ ` prompt. It dispatches the same `/bin/*.py` commands and shares `cwd` state with the parent session. Type `exit` to return to `>>>`:

```
>>> sh()
PythonOS shell: help | examples | desktop [APP] | exit
$ ls /bin
.  ..  ls.py  ps.py  pwd.py  cd.py  cat.py  cp.py  mv.py  ftp.py  ed.py  sysinfo.py  netstat.py  desktop.py  examples.py
$ cd /tmp
$ pwd
/tmp
$ help
$ examples
$ desktop --list
$ /examples/tone.py
$ exit
>>> cwd        # cwd change is visible back in Python
'/tmp'
```

Inside `sh()`, `.py` paths run directly, and `foo.py` resolves like
`./foo.py` from the current directory. Python files are native executables in
PythonOS.

`sh('cmd args')` is the single-shot form for scripted use:

```python
>>> sh('cp /bin/sysinfo.py /tmp/backup.py')
>>> sh('ls /tmp')
>>> sh('/examples/tone.py')
```

#### Writing your own commands

Any `.py` file placed in `/bin/` becomes a shell command automatically. Scripts run inside the kernel namespace — `vfs`, `scheduler`, `pci`, `net`, `OpenFlags`, and `print` are pre-bound. Top-level `await` is supported so scripts can call async VFS operations directly:

```python
# /bin/mycommand.py
# Invoked by typing: mycommand arg1 arg2
path = argv[0] if argv else cwd
entries = await vfs.readdir(path)
for e in entries:
    print(e)
```

`argv` is a list of string arguments. `cwd` is the current working directory string; a script can update it (`cwd = '/new/path'`) and the shell propagates the change back.

Create a new command at runtime:

```python
>>> fd = await vfs.open('/bin/hello.py', OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
>>> await vfs.write(fd, b"print('hello, ' + (argv[0] if argv else 'world'))\n")
>>> vfs.close(fd)
>>> hello
hello, world
>>> hello PythonOS
hello, PythonOS
```

#### Example programs

PythonOS also seeds `/examples` with readable Python programs that are frozen into the kernel as bytecode, so they can use normal functions, classes, async loops, and imports without depending on the limited runtime compiler:

```
>>> ls /examples
README.txt  async_tasks.py  hello_kernel.py  primes.py  recv_file.py  send_file.py  tone.py  vfs_demo.py
>>> run('/examples/hello_kernel.py')
>>> run('/examples/vfs_demo.py')
>>> run('/examples/async_tasks.py')
>>> sh('/examples/primes.py 100')
>>> run('/examples/tone.py')
```

The smaller examples cover shell arguments, scheduler/VFS inspection, TmpFS
read/write, cooperative asyncio tasks, and pure-Python computation.

The `ed` command is backed by `kernel.ed`, a line-oriented editor inspired by
`py_ed`. It supports append/insert/change/delete, print/number/literal print,
marks, move/copy, read/write/write-append, one-level undo, and `q`/`Q`.

The `ftp` command provides a small FTP-like file copy workflow over one raw TCP stream. It is not the RFC FTP protocol; it is a simple get/put tool built for the PythonOS shell and TmpFS:

```
# inbound to PythonOS; make run forwards host localhost:17000 to guest port 7000
>>> ftp get /tmp/inbox.txt
# host terminal:
$ nc localhost 17000 < local-file.txt
# run-arm64 forwards host localhost:17002 to guest port 7000

# outbound from PythonOS to the QEMU host at 10.0.2.2
# host terminal:
$ nc -l 7001 > from-pythonos.txt
>>> ftp put /tmp/inbox.txt
```

Use `FILE_HOST_PORT=<port> make run` if you need a different host-side
forwarded port.

The older file-transfer examples are still available as readable source at `/examples/recv_file.py` and `/examples/send_file.py`.

#### Mixing Python and shell

The two modes are additive. Anything that is a valid Python expression or statement still goes through the Python interpreter. The shell dispatch only fires for bare identifiers that are not already in the Python namespace:

```
>>> ls                        # shell dispatch → /bin/ls.py
>>> list(pci)                 # Python expression → PCI device list
>>> for d in pci: print(d)    # Python statement → loop over PCI bus
>>> sh()                      # enter shell sub-REPL
$ ps                          # shell command
$ exit                        # return to >>>
>>> scheduler.ps()            # back to Python
```

### Serial shell

The `>>>` prompt is also available on the serial console (the QEMU terminal window itself). All interactive I/O goes through COM1 (x86_64) or PL011 (arm64) — no PS/2, no framebuffer required.

### Run the smoke tests

```bash
make test           # default boot smoke (serial + TCP REPL based)
make test-gui       # GUI subsystem smoke (headless screendump + audio capture)
```

`make test` boots PythonOS in QEMU as a subprocess, waits for the TCP REPL to become reachable, runs a set of Python expressions through it, verifies the expected output, and exits with code 0 on pass. The x86_64 smoke test boots with two virtual CPUs by default; use `SMP_CPUS=4 make test` to expose more.

`make test-gui` runs three additional suites on x86_64 (or one on arm64):
- **gui smoke** — sdl2 corpus (`hello`/`renderer`/`text`/`image`/`jpeg`), compositor render, mouse pipeline, pointer round-trip, serial markers
- **desktop smoke** — boots, launches `desktop('bouncing_ball')`, screendumps, asserts pixel-exact desktop bg + title bar + window body + tile-hash golden
- **audio smoke** — boots with `-audiodev wav,id=a`, runs `examples/tone.py`, verifies the captured WAV header (48 kHz / 2ch / 16-bit)

Counts at HEAD: 55 / 37 / 26 / 5 / 6 / 8 across the six suites (default x86, default arm64, x86 gui, x86 desktop, x86 audio, arm64 gui) — **137 tests** total.

GitHub Actions runs that gate on **both** architectures: `ubuntu-24.04` builds `build/pythonos.iso` and runs the x86 serial + GUI smokes; `ubuntu-24.04-arm` builds `build-arm64/pythonos-arm64.elf` and runs the arm64 smokes. Each job uploads its bootable image. `make release` waits for that workflow, then attaches **both** files to the GitHub release.

For the no-GIL path, run `PYTHONOS_FREE_THREADING=1 SMP_CPUS=4 make test`.
That smoke covers the boot-time SMP self-tests, `_hal.pthread_selftest()`, and
`/examples/thread_demo.py`, including multiple Python worker threads and timed
lock acquisition.

---

## What Is This?

Most operating systems are written in C, with scripting languages bolted on top as userspace programs. PythonOS inverts that: the Python interpreter **is** the kernel primitive. There is no C runtime managing Python — Python manages the machine.

The philosophical bet: "everything is an object" is a better organizing principle for a kernel than "everything is a file." `importlib`, `exec`, and `inspect` become the OS hot-reload and introspection syscalls. The system can extend itself at runtime without rebooting.

### Boot Sequence

#### x86_64

```
GRUB2 (multiboot2)
  └─▶ boot.asm — long mode, 4 GiB identity map, 4 MiB stack
        └─▶ main.c — GDT, IDT, PIC (8259A), PIT (100 Hz), COM1 serial
              ├─▶ kthread.c — bootstrap-CPU native thread self-test
              ├─▶ smp.c — ACPI MADT discovery, AP startup, per-CPU state, native AP worker dispatch
              └─▶ hal.c — CPython init, freeze frozen modules
                    └─▶ kernel.boot() — Python owns the machine
                          ├─▶ PMM + VMM
                          ├─▶ PCI enumeration + driver binding
                          │     ├─▶ VirtIO-net (DMA descriptor rings)
                          │     └─▶ Intel HDA (BDL DMA, codec)
                          ├─▶ asyncio event loop (PIT-driven, 100 Hz)
                          ├─▶ Network stack (ARP/IP/TCP)
                          ├─▶ TCP REPL server (port 5000, multi-session)
                          ├─▶ COM1 serial input driver
                          └─▶ Interactive kernel shell (serial + TCP)
```

#### arm64 (QEMU `virt`)

```
U-Boot / QEMU direct kernel load
  └─▶ boot_arm64.asm — EL1, MMU off, 4 MiB stack
        └─▶ main_arm64.c — GIC v2, generic timer (100 Hz), PL011 serial
              └─▶ hal.c — CPython init, freeze frozen modules
                    └─▶ kernel.boot() — Python owns the machine
                          ├─▶ PMM + VMM
                          ├─▶ VirtIO-MMIO block device (raw disk image)
                          ├─▶ asyncio event loop (GIC timer-driven)
                          ├─▶ PL011 serial input driver
                          └─▶ Interactive kernel shell (serial)
```

### Expected Boot Output (x86_64)

```
[PythonOS] INFO  kernel.boot: interrupt router connected
[PythonOS] INFO  kernel.boot: PMM ready — 510 MiB free
[PythonOS] INFO  kernel.boot: VMM ready
[PythonOS] INFO  kernel.boot: enumerating PCI bus...
[PythonOS] INFO  kernel.boot: 7 PCI devices found
[PythonOS] INFO  kernel.boot: tmpfs mounted at /
[PythonOS] INFO  kernel.boot: event loop ready
[PythonOS] INFO  kernel: no framebuffer — serial only       # default boot
[PythonOS] INFO  kernel: COM1 serial input ready
[PythonOS] INFO  virtio-net: MAC 52:54:00:12:34:56
[PythonOS] INFO  kernel: network stack starting
[PythonOS] INFO  repl: TCP REPL listening on port 5000 — connect: nc localhost 5555
[PythonOS] INFO  kernel: HDA sound ready
[PythonOS] INFO  mixer: attached backend HDADriver
[PythonOS] INFO  kernel: shell spawned — system ready

PythonOS kernel shell
Python 3.14.0
Type help or help() for commands, demos, and examples.
Desktop: desktop()  desktop('pacmaze')  desktop('help')
Examples: examples()  run('/examples/hello_kernel.py')

>>>
```

In `make run-gui` mode the framebuffer + GUI input substrate also come up:

```
[PythonOS] boot: framebuffer found
[PythonOS] INFO  kernel: framebuffer console ready
[PythonOS] INFO  kernel: GUI input ready (PS/2 kbd+mouse)
```

On arm64 with `make run-gui-arm64` the equivalent markers are:

```
[PythonOS] INFO  ramfb: 1024x768x32 ready
[PythonOS] INFO  virtio-input: ready at 0xa003e00
[PythonOS] INFO  kernel: GUI input ready (virtio-input x2)
[PythonOS] INFO  virtio-snd: ready at 0xa003a00 (stream 0, 48000 Hz / 2ch / S16)
[PythonOS] INFO  kernel: virtio-snd ready
```

### Source Layout

```
src/
  boot/         asm + C bootstrap: GDT, IDT, PIC, PIT, serial, framebuffer (x86_64)
                boot_arm64.asm, main_arm64.c — GIC, generic timer (arm64)
                grub.cfg — GRUB menu (ISO staging copies this into build/iso/)
  hal/          hal.c — _hal Python C extension (port I/O, MMIO, interrupts, DMA)
  libc/         freestanding libc: buddy allocator, string, stdio, POSIX stubs
  linker.ld / linker_arm64.ld   ELF layouts for the two arches

build/                   x86_64 objects, pythonos.iso, disk.img (gitignored)
build-arm64/             arm64 objects and pythonos-arm64.elf (gitignored)

kernel/
  __init__.py        boot() entry point — wires all subsystems
  hal/               thin Python wrapper over _hal
  interrupts/        interrupt router, @interrupt decorator, default handlers
  bus/pci.py         PCI enumeration (CF8/CFC), driver Protocol, PCIBus
  display/           framebuffer + bitmap font console
  log.py             serial logging via _hal (arch-aware: COM1 / PL011)
  memory/            PMM (page frame allocator), VMM (virtual address spaces)
  drivers/
    input/
      com1.py        COM1 16550A serial input (x86_64 headless)
      pl011.py       PL011 UART input (arm64 virt)
      virtio_input.py virtio-input keyboard + mouse + tablet over MMIO (arm64)
    keyboard.py      PS/2 keyboard (x86; IRQ1, scancode set 1)
    mouse.py         PS/2 mouse (x86; IRQ12, 3-byte packet)
    net/
      virtio_net.py  VirtIO-net driver (DMA descriptor rings)
    block/
      virtio_blk.py  VirtIO-MMIO block driver (arm64)
    display/
      bochs.py       bochs-display PCI driver (x86 GUI)
      ramfb.py       arm64 ramfb via fw_cfg
      fwcfg.py       fw_cfg helper
    sound/
      virtio_snd.py  arm64 virtio-snd backend
  sound/
    hda.py           Intel HDA driver (BDL DMA, codec configuration)
    mixer.py         arch-neutral PCM playback API (Mixer)
  gui/                 GUI subsystem — opt-in, only active under run-gui
    input.py         canonical Event + EventQueue; PS/2 + virtio-input bridges
    compositor.py    stacking window manager (Tab focus, drag, click-to-focus)
    sdl2/            PySDL2-compatible Python API (Init, Window, Surface, Renderer,
                     events, sdlmixer, sdlttf — see docs/gui.md)
    image/           image decoders: bmp, ppm, png (with pure-Python deflate), jpeg
  net/
    ethernet.py      Ethernet frame encode/decode
    arp.py           ARP request/reply
    ip.py            IPv4 packet encode/decode, checksum
    tcp.py           TCP state machine (connect + listen/accept)
    stack.py         Network stack init, ARP/IP dispatch
    repl_server.py   Multi-session TCP REPL server (port 5000)
  fs/                VFS (POSIX-like fd table, CREAT/TRUNC, async protocol) + tmpfs
  ed.py              ed-style line editor used by /bin/ed.py
  scheduler.py       asyncio task scheduler (ps, spawn)
  shell.py           kernel shell: Python REPL + bare-word /bin dispatch + sh() sub-REPL

apps/                  built-in GUI applications (frozen, registered via apps.registry)
  terminal/          Python REPL inside a CompositorWindow
  editor/            ed line editor in a window
  files/             arrow-key file browser with TCP send/recv
  image_viewer/      BMP / PPM / PNG / JPEG viewer
  sysmon/            live kernel state — uptime, free RAM, processes
  about/             "About PythonOS" version + system info window
  clock/             big-digit uptime clock with bespoke 5x7 font
  demos/             bouncing_ball, audio_tone, starfield, rainfall,
                     plasma, paint, life, keyboard, mandelbrot, spirograph,
                     sprites, defender, pacmaze, raiders
  _textwin.py        shared text-grid view used by terminal + editor
  _icons.py          dock icon factories (48x48 SDL_Surface generators)
  registry.py        AppInfo / register / list_apps used by dock + menus

bin/  (seeded in tmpfs at boot — add .py files here to create new shell commands)
  ls.py, ps.py, pwd.py, cd.py, cat.py, cp.py, mv.py, ftp.py, ed.py — filesystem / transfer utilities
  sysinfo.py, netstat.py                                  — system / network status
  desktop.py, examples.py                                  — public catalogs / launchers
  pythonos_gui.py                                          — legacy framebuffer launcher

examples/          frozen runnable demos, also seeded as readable source in /examples
  hello_kernel.py, vfs_demo.py, async_tasks.py, primes.py, tone.py,
  recv_file.py, send_file.py
  fb_test.py, sdl_hello.py, sdl_renderer.py, sdl_text.py,
  sdl_image.py (PNG corpus), sdl_jpeg.py (JPEG corpus)

asyncio/             bare-metal asyncio (no socket/selectors): Future, Task,
                     Queue, Event, Lock, Semaphore, sleep, wait_for, gather

tests/
  smoke_test.py            x86 default smoke (TCP REPL based)
  smoke_test_arm64.py      arm64 default smoke (PL011 serial)
  gui_smoke_test.py        x86 GUI smoke (sdl2 corpus + compositor + pixel checks)
  gui_smoke_test_arm64.py  arm64 GUI smoke (ramfb + virtio-input + screendump)
  desktop_smoke_test.py    end-to-end desktop() launch + tile-hash golden
  audio_smoke_test.py      WAV-capture audio pipeline check
  qmp_helper.py            QEMU monitor wrapper (screendump, sendkey, mouse_*)
  goldens/x86_64/          checked-in tile-hash goldens (refresh: PYTHONOS_GOLDEN_REFRESH=1)

tools/
  freeze_kernel.py   compiles kernel + apps + examples → frozen C bytecode in the ELF
  run_gui.py     supervises QEMU + bridge and requests desktop auto-start
  stdlib_stubs/      bare-metal replacements for stdlib modules that assume
                     a POSIX host (dataclasses, functools, os, ctypes, sdl2, …)
  Dockerfile         Ubuntu 24.04 cross-compilation environment
  setup_cpython.sh   fetch, patch, and configure CPython 3.14 for bare metal

deps/
  Modules.Setup.local  CPython built-in module list (excludes socket/select/…)
  cpython/           built libpython3.14.a + headers (generated; not in git)
  cpython-src/       CPython 3.14.0 source (generated; not in git)
```

---

## Architecture Notes

### CPython on Bare Metal

CPython 3.14.0 is compiled as a static library (`libpython3.14.a`) linked directly into the kernel ELF. `Py_Initialize()` runs before any Python code; from that point forward, the Python interpreter is the kernel runtime.

All kernel Python modules are **frozen** — compiled to bytecode and embedded in the ELF as C arrays. There is no filesystem required for `import`. The freeze tool (`tools/freeze_kernel.py`) runs at build time and produces `build/frozen_kernel.c`.

### `_hal` C Extension

`src/hal/hal.c` implements the `_hal` built-in Python module, compiled into the kernel. It exposes:

| Function | Description |
|---|---|
| `inb/inw/inl(port)` | Port I/O reads (x86_64) |
| `outb/outw/outl(port, val)` | Port I/O writes (x86_64) |
| `mmio_read8/32(addr)` | Memory-mapped I/O reads |
| `mmio_write8/32(addr, val)` | Memory-mapped I/O writes |
| `dma_alloc(size)` | Allocate zeroed C-heap DMA buffer, return physical address |
| `buf_addr(bytearray)` | Return physical address of bytearray's internal buffer |
| `read_cr2/cr3()` | Control register reads (x86_64) |
| `set_interrupt_router(fn)` | Register Python interrupt dispatcher |
| `set_event_loop(loop)` | Register asyncio loop for interrupt-safe dispatch |
| `ARCH` | String constant: `"x86_64"` or `"arm64"` |

### DMA Memory

Python's garbage collector must never touch DMA buffers (device-visible memory). `_hal.dma_alloc(n)` allocates from the C buddy allocator (`calloc`) and returns an integer physical address. The GC cannot see this allocation. This is used by VirtIO-net descriptor rings, HDA BDLs, and RX packet buffers.

### Bare-Metal asyncio

The `asyncio/` package is a from-scratch implementation of the asyncio API — no `select`, no sockets, no `selectors`. The event loop is driven by the PIT timer on x86_64 or the GIC generic timer on arm64 (both at 100 Hz), with hardware interrupt callbacks routed through `call_soon_threadsafe`. Full API: `Future`, `Task`, `Queue`, `Event`, `Lock`, `Semaphore`, `sleep`, `wait_for`, `gather`, `ensure_future`.

### Multi-Session TCP REPL

The REPL server (`kernel/net/repl_server.py`) listens on TCP port 5000. Each incoming connection spawns a new `asyncio` task running an independent `Shell` instance. All sessions share the same live kernel namespace — `scheduler`, `vfs`, `pci_bus`, the network stack — because they are all coroutines inside the same Python process that *is* the kernel. This is the most direct possible demonstration that Python-as-kernel supports preemptive multitasking: multiple interactive sessions, zero threads, zero processes, one interpreter.

### Stdlib Stubs

Several stdlib modules assume a POSIX host and break on bare metal. `tools/stdlib_stubs/` contains replacements:

| Stub | Why it exists |
|---|---|
| `dataclasses.py` | CPython's version uses `exec()` to generate `__init__` etc., which fails on bare metal with a spurious `SyntaxError`. Rewritten using closures. |
| `functools.py` | `_CacheInfo = namedtuple(...)` uses `eval()`. Replaced with a plain class. |
| `os.py` | The real `os.py` imports `posix` which expects `_have_functions`. Minimal stub. |
| `ctypes/__init__.py` | `_ctypes` is not compiled in (requires libffi). Minimal stub for `addressof`; prefer `_hal.dma_alloc` for DMA. |
| `random.py` | Minimal LCG PRNG seeded from PIT port reads. |
| `traceback.py` | CPython 3.14's version imports `_colorize` (not compiled in). Minimal format_exc. |
| `linecache.py` | No source files on bare metal; no-op cache. |
| `inspect.py`, `pathlib.py` | Minimal stubs used during module discovery. |

Real stdlib files that *are* frozen verbatim: `enum`, `typing`, `operator`, `types`, `reprlib`, `keyword`, `copy`, `weakref`, `_weakrefset`, `contextlib`, `warnings`, `copyreg`, `struct`, `codeop`, `__future__`, `re`, `collections`.

---

## Building CPython for Bare Metal

The CPython configuration disables everything that requires a host OS:

- No `socket`, `select`, `selectors`, `ssl`, `readline`, `termios`
- No `fork`, `exec`, `subprocess`
- No dynamic loading (`dlopen`)
- Built-in modules only: `_struct`, `_collections`, `_functools`, `_io`, `_signal`, `math`, `_warnings`, `_weakref`, `_abc`, `_json`, `_csv`, `_datetime`, `_pickle`, `_random`, `_bisect`, `_heapq`, `_operator`, `_stat`, `array`, `binascii`, `zlib`, `_hashlib`, `_sha256`, `_sha512`, `_blake2`, `_md5`, and `_hal`

See `deps/Modules.Setup.local` for the complete list and `tools/setup_cpython.sh` for the configure flags.

---

## The Totally True and Not At All Embellished History of PythonOS

### The continuing adventures of Jordan Hubbard and Sir Reginald von Fluffington III

> *Part 6 of an ongoing chronicle.  [← Part 5: WebMux](https://github.com/jordanhubbard/webmux#the-totally-true-and-not-at-all-embellished-history-of-webmux)*
> *Sir Reginald von Fluffington III appears throughout.  He does not endorse any of it.*

It began, as many of the programmer's projects do, with a sentence that sounded completely reasonable at the time.

"The kernel," he announced to the living room at large, "should be written in Python."

Sir Reginald von Fluffington III was asleep on top of the programmer's copy of *Modern Operating Systems*, which he had been using as a mat for three weeks and had no intention of vacating. He did not open his eyes. He did not move. He did, however, lower one ear approximately four degrees — the feline equivalent of a raised eyebrow — before returning to the serious business of being unconscious.

The programmer took this as encouragement.

The idea was not new, exactly. Embedded Python had existed for years. MicroPython ran on microcontrollers. Plenty of people had run Python *on* operating systems. But running Python *as* the operating system — as the interrupt handler, the memory manager, the scheduler, the filesystem, the network stack — that was a different claim. The programmer called it "elegant." Sir Reginald, who had heard this word applied to seventeen previous decisions of varying quality, updated a private ledger entry he maintained under the heading *"Hubris, General."*

The first problem was CPython itself. The interpreter was designed to run on a POSIX host. It expected `fork`. It expected `select`. It expected a great many things that do not exist when the machine has just powered on and there is no OS beneath you. The programmer spent several weeks writing `configure` flags that deleted these expectations one at a time, like a careful editor removing every assumption that civilization exists. The result was `libpython3.14.a`: a static library containing the full Python interpreter, stripped of every syscall it had ever trusted, linked directly into the kernel ELF.

"No `socket`. No `readline`. No `fork`," the programmer told Sir Reginald, presenting the `Modules.Setup.local` like a trophy. Sir Reginald examined it by sitting on it. Then he sat on the keyboard. The programmer, interpreting this as a request for a demonstration, moved the cat, opened a terminal, and typed `make run`.

The kernel booted. Python initialized. The `>>>` prompt appeared on the serial console. Sir Reginald, who had relocated to the cable routing behind the monitor, was unavailable for comment.

What made the project genuinely strange was the interrupt model. In a normal OS, hardware interrupts are handled in C, which carefully manages processor state before dispatching to higher-level code. In PythonOS, the C bootstrap handles the raw vector, then immediately calls `asyncio.get_event_loop().call_soon_threadsafe(interrupt_router, vector)` — and Python takes it from there. The PIT timer fires 100 times a second. The GIC fires 100 times a second on arm64. Both route through the same Python `@interrupt` decorator. The scheduler is `asyncio`. The kernel shell is `await shell.run()`. The entire operating system is, at runtime, a set of coroutines.

Sir Reginald was not impressed by this. He had watched the programmer write asynchronous code before and knew where it ended: debugging `asyncio.ensure_future` at two in the morning while muttering about the event loop.

The arm64 port required a separate detour. QEMU's `virt` machine has no PCI bus — only VirtIO-MMIO, a GIC interrupt controller, and a PL011 UART. The programmer ported the timer, the serial driver, and the block device by reading ARM Architecture Reference Manual sections that Sir Reginald found structurally identical to the programmer's earlier operating systems reading in that both involved large books and produced no tuna.

The TCP REPL was the part the programmer was most pleased with. Having added a TCP stack from scratch — ARP resolution, IPv4, a basic TCP state machine with SYN/SYN-ACK/ACK, FIN handling, a `TCPListener` class — he wired it to the kernel shell and exposed it on port 5000. QEMU forwarded host port 5555 to guest port 5000. `nc localhost 5555` connected to a live Python interpreter that *was* the kernel. Multiple sessions could run simultaneously. Each session shared the same live kernel objects — `vfs`, `scheduler`, the PCI bus — because they were all coroutines in the same process that happened to be the operating system.

"This," the programmer said, "proves that Python is a real multitasking kernel."

Sir Reginald walked across the keyboard. The shell printed `TypeError: unsupported operand` and terminated. The programmer noted this was a separate bug and filed it accordingly.

The smoke test was added last, as things tend to be when the thing being tested is a kernel that takes forty-five seconds to compile and requires QEMU to run. `tests/smoke_test.py` boots the ISO as a subprocess, waits for the TCP REPL to become reachable, connects, and verifies that `1 + 1` returns `2`, that `vfs is not None` returns `True`, that `1 / 0` raises `ZeroDivisionError`, and that the kernel's scheduler and filesystem are alive and accessible from a remote TCP session. If any of these fail, `make test` exits non-zero. Sir Reginald has never run `make test`. He has, however, sat on the test output twice, which the programmer is counting as a code review.

As of this writing, PythonOS has been used in production by exactly one person, who also wrote it. Sir Reginald continues to withhold his endorsement across all 6 projects, citing "procedural concerns," "insufficient tuna," "a general atmosphere of hubris," and, now, "the fundamental unseriousness of an operating system that can be interrupted by the garbage collector."

---

## License

BSD 2-Clause. See [LICENSE](LICENSE).

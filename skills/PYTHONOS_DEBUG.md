# PythonOS build, run, test, and debug

Use this skill for PythonOS correctness, GUI, networking, native-kernel, and
performance investigations. It is written for coding CLIs and agents.

## Build and test

Run from the repository root. `make` builds the host-native distribution using
Docker; rebuild the host bridge when GUI code changed.

```bash
make
make bridge
make test-chipset       # host chipset, arcade, dock, layout, CI-gate tests
make test-bridge        # host SDL bridge protocol and metrics tests
make test-gui           # QEMU GUI integration, when QEMU is available
```

Use `make TARGET_ARCH=x86_64` or `make TARGET_ARCH=arm64` to choose the
target. Do not claim a build is current until `make` has run after the relevant
source changes.

## Desktop operating modes

Set `PYTHONOS_DESKTOP_MODE` for every GUI run.

| Mode | Launch | Purpose |
|---|---|---|
| `interactive` (default) | `PYTHONOS_DESKTOP_MODE=interactive make run-gui` | Opens, raises, and focuses the host SDL/Cocoa desktop for a person to watch and use. Run from the user’s visible desktop terminal. |
| `headless` | `PYTHONOS_DESKTOP_MODE=headless PYTHONOS_DEBUG=1 make run-gui` | Creates a hidden SDL surface for autonomous capture, input injection, regression checks, and performance work. Never describe it as user-visible. |

Both modes run the same guest compositor and host bridge. `capture` works in
both. In headless mode it is the visual inspection mechanism. In interactive
mode the user is the visibility authority; a successful surface capture alone
does not prove the window is onscreen.

## Debug launch and attachment

Use the debug envelope for any nontrivial diagnosis:

```bash
PYTHONOS_DEBUG=1 PYTHONOS_DESKTOP_MODE=headless make run-gui
PYTHONOS_DEBUG=1 PYTHONOS_DESKTOP_MODE=interactive make run-gui
```

It writes `build/pythonos-debug.json`: REPL, native remote endpoint, QMP,
symbol ELF, serial log, selected desktop mode, and desktop co-process PID/log.

```bash
python3 tools/pythonos_debug.py session
python3 tools/pythonos_debug.py status
python3 tools/pythonos_debug.py serial --lines 200
python3 tools/pythonos_debug.py qmp status
python3 tools/pythonos_debug.py native -- "bt" "info registers"
python3 tools/pythonos_debug.py desktop status
python3 tools/pythonos_debug.py desktop metrics
python3 tools/pythonos_debug.py desktop native -- "thread backtrace all"
```

The native endpoint uses QEMU’s GDB-remote wire protocol internally; the
agent-facing plane is called `native`. QMP, serial, and native attachment work
even when guest networking or CPython is broken.

## Drive and inspect the GUI

Use the public launcher, then normalized desktop-relative input. App handlers
receive mouse coordinates relative to their drawable body; title bars belong
to the compositor.

```bash
python3 tools/pythonos_debug.py launch paint
python3 tools/pythonos_debug.py mouse move 180 180
python3 tools/pythonos_debug.py mouse down 180 180
python3 tools/pythonos_debug.py mouse move 260 220
python3 tools/pythonos_debug.py mouse up 260 220
python3 tools/pythonos_debug.py key esc
python3 tools/pythonos_debug.py capture build/pythonos-debug.bmp
```

`exercise NAME` resets metrics, launches an app, drags a title bar, injects
pointer/keyboard input, exits with ESC, and prints a performance snapshot. Use
it only in headless mode or a session explicitly ceded to automation.

## Performance and stalls

`perf` reports guest-observed RPC round trips; `desktop metrics` reports host
bridge service time and `slow_ops`. An operation over 10 ms logs
`desktop-main blocked`, meaning the SDL main thread cannot process cursor,
window, or input events and may show a busy cursor.

```bash
python3 tools/pythonos_debug.py perf --reset
python3 tools/pythonos_debug.py exercise paint
python3 tools/pythonos_debug.py desktop metrics
```

If both guest RTT and host service are high, optimize the bridge/SDL operation.
If host service is low but guest RTT is high, inspect guest transport,
scheduler, and QEMU. Stop via QMP and inspect native/co-process stacks when a
spike aligns with serial warnings.

## Input invariant

`kernel.gui.input.EVENT_KEY_DOWN` and `EVENT_KEY_UP` are event kinds.
`KEY_UP` and `KEY_DOWN` are arrow key codes. Never compare `Event.kind` to an
arrow-key code.

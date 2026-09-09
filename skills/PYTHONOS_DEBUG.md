# PythonOS remote debugging

Use this skill when an app, demo, GUI interaction, scheduler task, or kernel
subsystem needs to be diagnosed in a running PythonOS VM. It is intended for
coding agents, not an interactive human workflow.

## Attach

Boot in native-debug mode (`PYTHONOS_DEBUG=1 make run-gui` for GUI work),
then use the host-side debugger:

```bash
python3 tools/pythonos_debug.py status
python3 tools/pythonos_debug.py eval "scheduler.tasks"
python3 tools/pythonos_debug.py launch paint
python3 tools/pythonos_debug.py mouse move 220 180
python3 tools/pythonos_debug.py mouse down 220 180
python3 tools/pythonos_debug.py key esc
python3 tools/pythonos_debug.py serial
python3 tools/pythonos_debug.py qmp stop
python3 tools/pythonos_debug.py native -- "bt" "info registers"
python3 tools/pythonos_debug.py exercise paint
python3 tools/pythonos_debug.py perf
python3 tools/pythonos_debug.py capture build/pythonos-debug.bmp
```

Debug mode writes `build/pythonos-debug.json`, which records the local TCP
REPL endpoint, native remote endpoint, QMP socket, symbol ELF, and captured
serial log. QEMU exposes the native endpoint using the GDB remote wire
protocol; that is transport compatibility, not the PythonOS agent interface.
The client uses the existing guest TCP REPL forwarded to host port 5555
(5556 for the standard arm64 target) for Python-level commands. It opens a
fresh session per command and exits nonzero if the guest is unreachable.

The `serial`, `qmp`, and `native` commands remain usable when networking or
CPython itself is broken. Set `PYTHONOS_DEBUG_PAUSE=1` to start QEMU halted;
use `qmp cont` or `native` to inspect early boot before continuing.

## Agent workflow

1. Start with `status`, then inspect the concrete live object that owns the
   behavior: `scheduler.tasks`, `compositor._windows`, `chipset.active_view`,
   or `kernel.gui.input.pointer_position()`.
2. Launch through the public surface (`launch NAME`, equivalent to
   `desktop('NAME')`) so registry and lifecycle bugs are visible.
3. Query logs or state using one-line `eval` / `exec` calls. Do not scrape the
   SDL window or infer crashes from an absent visual alone.
4. Exercise input through the active environment with `key` and `mouse`, then
   inspect state before and after. Mouse arguments are desktop-relative; GUI
   windows receive coordinates local to their drawable body (the title bar is
   not part of an app surface).
5. Reproduce with a host-side test where possible. Run `make test-chipset` for
   chipset, arcade, dock, layout, and gate coverage; run `make test-gui` when
   QEMU is available.

## Autonomous E2E loop

Use `exercise NAME` for a deterministic agent-owned GUI pass: it resets bridge
metrics, launches the named registry entry, drives mouse and keyboard events,
returns with ESC, and prints a combined performance snapshot. `perf` reports
two distinct measurements: guest-observed RPC round trips (including guest
transport/scheduling) and host bridge service time per operation. Compare
these before and after a change; stop via QMP and inspect native stacks when a
latency spike coincides with a stall or warning in `serial`.

Use `capture PATH` to save the actual host SDL desktop as a BMP. Inspect that
image directly after every visual workload; QEMU screendumps do not see bridge
mode because the host companion, not QEMU, owns the visible window.

## Input invariants

`kernel.gui.input.EVENT_KEY_DOWN` and `EVENT_KEY_UP` are event kinds.
`KEY_UP` and `KEY_DOWN` are arrow key codes. Never compare `Event.kind` to an
arrow-key code. This distinction is critical for launch loops, ESC handling,
and games that retain a pressed-key set.

## Constraints

The TCP REPL is a privileged debugging interface; bind it only through QEMU's
loopback host forwarding. Keep debug expressions single-line. For multi-step
experiments, add a temporary helper under the workspace and invoke it with the
REPL rather than attempting to paste a multiline session.

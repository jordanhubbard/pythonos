---
namespace: pythonos
version: 1.0.0
display_name: PythonOS Core
profiles: []
sample: false
provides: []
requires: []
authoring_inputs:
  - kind: specification-to-source-skill
    uri: skills/specification-to-source/pythonos-kernel/SKILL.md
workflow_definition: workflows/specification-to-source.md
routing_policy: routing/specification-to-source.json
flavor_slots:
  - slot_id: build-system
    axis: build.system
    cardinality: exactly-one
    capability_contract: build.policy.make
  - slot_id: target-runtime
    axis: deployment
    cardinality: exactly-one
    capability_contract: deployment.qemu
  - slot_id: os
    axis: platform.os
    cardinality: exactly-one
    capability_contract: application.portable-json
entrypoints:
  - name: run
    kind: bootable-iso
    path: pythonos.iso
acceptance_contracts: []
source_dependencies: []
---

# PythonOS Core

## Objective

A bare-metal operating system where CPython 3.14 is the kernel — not a program
running on an OS, but the OS itself. Python owns the machine from interrupt handlers
to the interactive shell. Produces a bootable ISO (`pythonos.iso`) that runs on
x86_64 and arm64 QEMU virtual machines.

## Scope

- Boots directly to a Python interactive REPL (`>>>`) on the serial console and,
  optionally, on a framebuffer window.
- The REPL is a real CPython interpreter with full access to kernel internals
  (`scheduler`, `vfs`, `sh()`, `run()`).
- An optional GUI desktop provides a stacking compositor, built-in apps (terminal,
  editor, file browser, image viewer, system monitor, about, clock), and demos.
- Supports x86_64 and arm64 target architectures; host hardware acceleration (HVF on
  macOS, KVM on Linux) is used when guest and host arch match.

## Public interface

### Boot artifacts

| Artifact | Description |
|----------|-------------|
| `pythonos.iso` | Bootable ISO image (x86_64) |
| `pythonos-arm64.elf` | ELF kernel image (arm64 QEMU `virt` machine) |

### TCP REPL

The kernel exposes a TCP server on guest port 5000 (forwarded to host port 5555 by
default). Clients send Python expressions as UTF-8 text and receive the `repr()` of
the result followed by `\r\n`. The REPL is the primary programmatic interface for
testing and automation.

### REPL builtins

| Name | Signature | Description |
|------|-----------|-------------|
| `scheduler` | object | The running kernel scheduler instance |
| `vfs` | object | The virtual filesystem instance |
| `sh(cmd)` | `str → None` | Execute a `/bin` shell command |
| `run(path)` | `str → None` | Execute a Python script from the VFS |

### Build targets

| `make` target | Description |
|---------------|-------------|
| `make` / `make all` | Build ISO for host architecture |
| `make run` | Build and boot in QEMU (serial, no GUI) |
| `make run-gui` | Build and boot with framebuffer desktop |
| `make test` | Build, boot, run smoke suite, exit 0 on pass |
| `make stop` | Kill running QEMU instance |
| `make clean` | Remove build artifacts (keep libpython cache) |
| `make cleanall` | Remove everything including libpython cache |
| `make x86_64` / `make arm64` | Explicit per-arch build |

## Behavior and invariants

1. The OS boots to a Python `>>>` prompt within 90 seconds in QEMU.
2. The TCP REPL on port 5000 accepts connections within 90 seconds of boot.
3. Arithmetic, string operations, list comprehensions, and standard Python expressions
   evaluate correctly in the REPL.
4. `type(scheduler).__name__` evaluates to `"Scheduler"`.
5. `vfs is not None` evaluates to `True`.
6. `1 / 0` raises `ZeroDivisionError` (standard Python exception semantics).
7. `run('/bin/sysinfo.py')` produces output containing `"PythonOS"`.
8. `run('/bin/netstat.py')` produces output containing `"Interface"`.
9. `sh('ps')` produces output containing `"kshell"` (the kernel shell process).
10. All SMP CPUs declared via `SMP_CPUS` come online (verified by serial log marker
    `SMP online N/N`).
11. The build is reproducible within the Docker cross-compilation environment;
    the output ISO byte-matches across identical Docker image versions.

## Errors

- `make test` exits non-zero if any `[FAIL]` line appears in the smoke test output.
- The smoke test exits with code 1 if the ISO is not found, the REPL does not become
  reachable within `BOOT_TIMEOUT` seconds, or any test expression does not contain
  its expected substring.
- Boot failure (kernel panic or QEMU crash) surfaces as a missing REPL connection
  within the timeout, reported as `[FAIL] TCP REPL never became reachable`.

## Known gaps (not yet specced)

- VFS persistence: ext2 mount/unmount behavior and cross-reboot file persistence are
  tested in `tests/ext2_test.py` but not yet captured as Component invariants here.
- Audio: audio output behavior is smoke-tested but audio-specific invariants (tone
  frequency, sample rate, buffer underrun behavior) are not yet specified.
- GUI compositor: window stacking, focus, drag, and close-box behavior are documented
  in `docs/gui.md` but not yet captured as Component acceptance criteria.
- Free-threading (no-GIL) build: available on x86_64 via `PYTHONOS_FREE_THREADING=1`
  but behavioral differences from the GIL build are not yet specified.

## Acceptance

- [ ] `make test` exits 0 (smoke suite PASS for all 10 TCP REPL cases)
- [ ] `make test-arm64` exits 0 (ARM64 smoke suite passes)
- [ ] Boot reaches REPL within 90 seconds
- [ ] `type(scheduler).__name__` → `"Scheduler"` via TCP REPL
- [ ] `vfs is not None` → `True` via TCP REPL
- [ ] `run('/bin/sysinfo.py')` output contains `"PythonOS"`
- [ ] SMP serial marker `"SMP online N/N"` present for declared `SMP_CPUS`

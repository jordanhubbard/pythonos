#!/usr/bin/env python3
"""
Boot + interactive smoke test for PythonOS arm64.

Boots the kernel ELF under QEMU virt with PL011 connected to stdio,
then drives the kernel shell over those pipes — arm64 has no TCP REPL
(no PCI on virt and no MMIO net driver yet) so we talk directly to
the serial console. The same connection captures the early boot log
so we can also assert on the "kernel reached main loop" markers.

Asserts:
  * Boot reaches kernel.boot main loop with both CPUs online (-smp 2).
  * _hal.SMP_ONLINE reports the right number.
  * pthread coverage example runs all six sections to passed=6/6
    (lifecycle, identity, tss, lock, capacity, attr).

Usage:
    python3 tests/smoke_test_arm64.py [path/to/pythonos-arm64.elf]
"""

import os
import platform
import re
import subprocess
import sys
import time

ELF = sys.argv[1] if len(sys.argv) > 1 else "pythonos-arm64.elf"
DISK = (os.environ.get("PYTHONOS_DISK")
        or os.environ.get("PYTHONOS_ARM64_DISK")
        or "build/disk.img")
SMP_CPUS = os.environ.get("PYTHONOS_ARM64_SMP_CPUS", "2")
BOOT_TIMEOUT = float(os.environ.get("PYTHONOS_ARM64_BOOT_TIMEOUT", "60"))
RECV_TIMEOUT = float(os.environ.get("PYTHONOS_ARM64_RECV_TIMEOUT", "60"))


def _qemu_accel_for(target_arch: str) -> list:
    host_machine = platform.machine().lower()
    host_arch = "arm64" if host_machine in ("arm64", "aarch64") else "x86_64"
    if host_arch != target_arch:
        return ["-cpu", "qemu64" if target_arch == "x86_64" else "cortex-a57"]
    if target_arch == "arm64":
        # HVF on Apple Silicon needs GICv3 (pythonos-nz1); stay on TCG.
        return ["-cpu", "cortex-a57"]
    accel = "hvf" if platform.system() == "Darwin" else "kvm"
    return ["-cpu", "host", "-accel", accel]


QEMU_CMD = [
    "qemu-system-aarch64",
    "-machine", "virt",
    *_qemu_accel_for("arm64"),
    "-m", "2G", "-smp", SMP_CPUS,
    "-no-reboot", "-no-shutdown",
    "-display", "none",
    "-monitor", "none",
    "-serial", "stdio",
    "-drive", f"if=none,file={DISK},format=raw,id=hd0",
    "-device", "virtio-blk-device,drive=hd0",
    "-kernel", ELF,
]


BOOT_MARKERS = [
    ("boot: serial",        "[PythonOS/arm64] boot: serial OK"),
    ("boot: MMU enabled",   "[PythonOS/arm64] boot: MMU enabled"),
    ("boot: TLS",           "[PythonOS/arm64] boot: TLS initialized"),
    ("boot: VBAR",          "[PythonOS/arm64] boot: VBAR set"),
    ("boot: GIC",           "[PythonOS/arm64] boot: GIC initialized"),
    ("boot: timer",         "[PythonOS/arm64] boot: timer started"),
    ("boot: SMP online=N",  f"boot: SMP init complete, online={SMP_CPUS}"),
    ("boot: Python kernel", "[PythonOS/arm64] boot: starting Python kernel"),
    ("hal: AppendInittab",  "[hal] AppendInittab"),
    ("hal: Py_Initialize",  "[hal] Py_Initialize done"),
    ("hal: kernel imported","[hal] kernel imported"),
    ("kernel.boot: starting", "kernel.boot: starting"),
    ("kernel.boot: PMM",    "kernel.boot: PMM ready"),
    ("kernel.boot: VMM",    "kernel.boot: VMM ready"),
    ("kernel.boot: tmpfs",  "kernel.boot: tmpfs mounted"),
    ("kernel.boot: main loop", "kernel.boot: entering main loop"),
    ("kernel: PL011 ready", "kernel: PL011 serial input ready"),
]


# Bytes-or-str helper: stdio from Popen is bytes when bufsize=0/text=False.
def _decode(b):
    if isinstance(b, (bytes, bytearray)):
        return b.decode("utf-8", errors="replace")
    return b


def _read_until(proc, needle: str, timeout: float, accumulator: list,
                trace: bool = False, search_from: int = 0) -> bool:
    """Read stdout from `proc` until `needle` appears in the accumulator
    AT OR AFTER character index `search_from`. Returns True on success,
    False on timeout or proc exit. Bytes read are appended to
    `accumulator` so the caller can inspect."""
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno()
    import select
    # Check if the needle is already past the offset we care about.
    if needle in "".join(accumulator)[search_from:]:
        return True
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return False
        if not chunk:
            return False
        text = _decode(chunk)
        accumulator.append(text)
        if trace:
            sys.stdout.write(text)
            sys.stdout.flush()
        if needle in "".join(accumulator)[search_from:]:
            return True
    return False


def _send(proc, data: str) -> None:
    proc.stdin.write(data.encode("utf-8") if isinstance(data, str) else data)
    proc.stdin.flush()


def run() -> int:
    if not os.path.exists(ELF):
        print(f"[FAIL] arm64 ELF not found: {ELF}")
        return 1
    if not os.path.exists(DISK):
        print(f"[FAIL] arm64 disk image not found: {DISK}")
        return 1

    print(f"[smoke-arm64] Starting QEMU with {ELF} (-smp {SMP_CPUS}) ...")
    proc = subprocess.Popen(QEMU_CMD,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            bufsize=0)

    try:
        stream: list = []

        # ── Phase 1: wait for the kernel to land in its main loop ────────────
        if not _read_until(proc, "kernel: PL011 serial input ready",
                           BOOT_TIMEOUT, stream):
            print("[FAIL] kernel never reached PL011 serial input ready")
            print("--- captured output ---")
            print("".join(stream)[-4000:])
            return 1

        # Wait for the shell prompt so subsequent stdin lands on the REPL.
        if not _read_until(proc, ">>> ", BOOT_TIMEOUT, stream):
            print("[FAIL] shell prompt never appeared")
            print("--- captured output ---")
            print("".join(stream)[-4000:])
            return 1

        captured = "".join(stream)
        passed = 0
        failed = 0
        for label, substr in BOOT_MARKERS:
            if substr in captured:
                print(f"[PASS] {label:30s} -> found {substr!r}")
                passed += 1
            else:
                print(f"[FAIL] {label:30s} -> missing {substr!r}")
                failed += 1

        # ── Phase 2: drive the shell over the same serial pipe ───────────────
        # Send a couple of basic Python expressions to make sure the REPL is
        # responsive, then run pthread_coverage and assert all six markers.
        # The kernel's serial driver wraps output at 80 columns, so
        # substring matching has to ignore embedded \r / \n that fall mid-token.
        def _flat() -> str:
            return "".join(stream).replace("\r", "").replace("\n", "")

        # Snapshot the stream length so each command's response can be
        # examined in isolation (avoids accidentally matching earlier
        # output and lets us detect the *next* >>> prompt reliably).
        def _send_and_wait(expr: str, end: str = ">>> ",
                           timeout: float = RECV_TIMEOUT) -> str:
            mark = sum(len(s) for s in stream)
            _send(proc, expr)
            if not _read_until(proc, end, timeout, stream,
                               search_from=mark):
                return ""
            after = "".join(stream)[mark:]
            return after.replace("\r", "").replace("\n", "")

        for expr, expected in [
            ("1 + 1\n", "2"),
            (f"__import__('_hal').SMP_ONLINE\n", SMP_CPUS),
            ("__import__('_hal').ARCH\n", "'arm64'"),
        ]:
            response = _send_and_wait(expr)
            if not response:
                print(f"[FAIL] shell did not respond to {expr.strip()!r}")
                failed += 1
                break
            if expected in response:
                print(f"[PASS] {expr.strip()!r:40s} -> found {expected!r}")
                passed += 1
            else:
                print(f"[FAIL] {expr.strip()!r:40s} -> expected {expected!r}")
                print(f"       got: {response[:200]!r}")
                failed += 1

        entered_sh = _send_and_wait("sh()\n", end="$ ")
        if not entered_sh:
            print("[FAIL] sh() did not enter shell mode")
            failed += 1
        else:
            cd_response = _send_and_wait("cd /bin\n", end="$ ")
            tab_response = _send_and_wait("sys\t\n", end="cwd: /bin")
            prompt_response = _send_and_wait("", end="$ ")
            exit_response = _send_and_wait("exit\n", end=">>> ")
            if (not cd_response or not tab_response or
                    not prompt_response or not exit_response):
                print("[FAIL] sh() tab-completion flow did not return prompts")
                failed += 1
            elif "PythonOS" in tab_response:
                print("[PASS] sh() filename tab completion          -> ran sysinfo.py")
                passed += 1
            else:
                print("[FAIL] sh() filename tab completion          -> expected PythonOS")
                print(f"       got: {tab_response[:200]!r}")
                failed += 1

        # pthread_coverage exercises multiple workers and repeated lock
        # cycles — give it a generous deadline.
        cov = _send_and_wait("run('/examples/pthread_coverage.py')\n",
                              timeout=max(RECV_TIMEOUT, 180.0))
        if not cov:
            print("[FAIL] pthread_coverage.py never returned to shell prompt")
            failed += 1
        else:
            cov_markers = ("lifecycle ok", "identity ok", "tss ok",
                           "lock ok", "capacity ok", "attr ok",
                           "pthread coverage done passed=6/6")
            for marker in cov_markers:
                if marker in cov:
                    print(f"[PASS] pthread_coverage: {marker!r}")
                    passed += 1
                else:
                    print(f"[FAIL] pthread_coverage: missing {marker!r}")
                    failed += 1

        print(f"\n[smoke-arm64] {passed} passed, {failed} failed")
        if failed:
            print("--- captured output (tail) ---")
            print("".join(stream)[-4000:])
        return 0 if failed == 0 else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(run())

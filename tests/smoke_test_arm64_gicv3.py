#!/usr/bin/env python3
"""
arm64 boot smoke driving the GICv3 path explicitly.

The default smoke (`tests/smoke_test_arm64.py`) runs under TCG with
`-cpu cortex-a57 -machine virt`, which gives QEMU's GICv2 emulation —
that exercises the kernel's GICv2 driver. This sibling forces GICv3
by asking for cortex-a76 + `-machine virt,gic-version=3`, which lands
on the GICv3 driver in `src/boot/gic_arm64.c`.

The kernel auto-detects which version is present via GICD_PIDR2; this
script just confirms the v3 path boots cleanly to the kernel's main
loop. It runs under TCG too, so no Apple-Silicon HVF dependency.
"""

import os
import subprocess
import sys
import time


ELF  = sys.argv[1] if len(sys.argv) > 1 else "pythonos-arm64.elf"
DISK = os.environ.get("PYTHONOS_ARM64_DISK", "disk-arm64.img")
SMP_CPUS = os.environ.get("PYTHONOS_ARM64_GICV3_SMP_CPUS", "2")
BOOT_TIMEOUT = float(os.environ.get("PYTHONOS_ARM64_GICV3_BOOT_TIMEOUT", "30"))

LOG = "/tmp/pythonos-arm64-gicv3.log"


QEMU_CMD = [
    "qemu-system-aarch64",
    "-machine", "virt,gic-version=3",
    "-cpu", "cortex-a76",
    "-m", "2G", "-smp", SMP_CPUS,
    "-no-reboot", "-no-shutdown",
    "-display", "none",
    "-monitor", "none",
    "-serial", f"file:{LOG}",
    "-drive", f"if=none,file={DISK},format=raw,id=hd0",
    "-device", "virtio-blk-device,drive=hd0",
    "-kernel", ELF,
]


# Boot markers that prove the GICv3 init + the existing kernel boot
# pipeline both succeed. We assert on a representative subset rather
# than every line because the v3 path doesn't change the rest of the
# kernel's startup messages.
MARKERS = [
    "[PythonOS/arm64] boot: serial OK",
    "[PythonOS/arm64] boot: MMU enabled",
    "[PythonOS/arm64] boot: TLS initialized",
    "[PythonOS/arm64] boot: VBAR set",
    "[PythonOS/arm64] boot: GIC initialized",
    "[PythonOS/arm64] boot: timer started",
    "kernel.boot: starting",
    "kernel.boot: PMM ready",
    "kernel.boot: VMM ready",
    "kernel.boot: tmpfs mounted",
    "kernel.boot: entering main loop",
]


def main() -> int:
    if os.path.exists(LOG):
        try: os.remove(LOG)
        except OSError: pass

    print(f"[smoke-arm64-gicv3] booting {ELF} under TCG with gic-version=3 / cortex-a76")
    proc = subprocess.Popen(QEMU_CMD,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    try:
        deadline = time.time() + BOOT_TIMEOUT
        passes = 0
        fails  = 0
        seen: set = set()

        while time.time() < deadline and len(seen) < len(MARKERS):
            try:
                with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                for m in MARKERS:
                    if m not in seen and m in text:
                        seen.add(m)
            except OSError:
                pass
            if len(seen) < len(MARKERS):
                time.sleep(0.5)

        for m in MARKERS:
            ok = m in seen
            short = m if len(m) < 60 else m[:57] + "..."
            if ok:
                print(f"[PASS] {short}")
                passes += 1
            else:
                print(f"[FAIL] {short} (not found within {BOOT_TIMEOUT:.0f}s)")
                fails += 1

        print(f"\n[smoke-arm64-gicv3] {passes} passed, {fails} failed")
        return 0 if fails == 0 else 1
    finally:
        proc.terminate()
        try: proc.wait(timeout=4)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())

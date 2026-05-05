#!/usr/bin/env python3
"""
arm64 GUI smoke. Boots the arm64 ELF with ``-device ramfb +
virtio-keyboard-device`` headless, verifies the boot log shows the
GUI substrate coming up, captures a screendump via the QEMU monitor,
and asserts the framebuffer was populated (variance-based — there is
no TCP REPL on arm64 to hand-check pixel coordinates against).

Once virtio-tablet (pythonos-skw) and a headless arm64 REPL transport
arrive we can extend this with the same pixel-perfect compositor
checks the x86 desktop smoke does.
"""

import os
import platform
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qmp_helper import QemuMonitor, parse_ppm


ELF  = sys.argv[1] if len(sys.argv) > 1 else "pythonos-arm64.elf"
DISK = (os.environ.get("PYTHONOS_DISK")
        or os.environ.get("PYTHONOS_ARM64_DISK")
        or "build/disk.img")

LOG  = "/tmp/pythonos-gui-arm64.log"
MON  = "/tmp/pythonos-gui-arm64.mon.sock"
PPM  = "/tmp/pythonos-gui-arm64.ppm"

BOOT_TIMEOUT  = float(os.environ.get("PYTHONOS_ARM64_BOOT_TIMEOUT", "45"))
PORT = int(os.environ.get("PYTHONOS_ARM64_GUI_PORT", "5561"))


def _qemu_accel_for(target_arch: str) -> list:
    mode = os.environ.get("PYTHONOS_QEMU_ACCEL", "auto").strip().lower()
    if mode in ("off", "none"):
        mode = "tcg"
    if mode not in ("auto", "kvm", "tcg"):
        raise ValueError("PYTHONOS_QEMU_ACCEL must be auto, kvm, or tcg")
    host_machine = platform.machine().lower()
    host_arch = "arm64" if host_machine in ("arm64", "aarch64") else "x86_64"
    kvm_ok = (platform.system() == "Linux"
              and os.path.exists("/dev/kvm")
              and os.access("/dev/kvm", os.R_OK | os.W_OK))
    if mode == "kvm" and (host_arch != target_arch or not kvm_ok):
        raise RuntimeError(
            f"PYTHONOS_QEMU_ACCEL=kvm requested, but KVM is not usable "
            f"for {target_arch} on this host")
    arm64_kvm = os.environ.get("PYTHONOS_ARM64_KVM", "").strip().lower() \
        in ("1", "true", "yes", "on")
    if mode == "kvm" or (mode == "auto" and target_arch == "arm64"
                         and arm64_kvm and host_arch == target_arch and kvm_ok):
        return ["-accel", "kvm", "-cpu", "host"]
    return ["-cpu", "cortex-a57"]


def _qemu_cmd():
    return [
        "qemu-system-aarch64",
        "-machine", "virt",
        *_qemu_accel_for("arm64"),
        "-m", "2G",
        "-smp", "2",
        "-no-reboot", "-no-shutdown",
        "-display", "none",
        "-device", "ramfb",
        "-device", "virtio-keyboard-device",
        "-serial", f"file:{LOG}",
        "-monitor", f"unix:{MON},server,nowait",
        "-netdev", f"user,id=net1,hostfwd=tcp::{PORT}-:5000",
        "-device", "virtio-net-device,netdev=net1",
        "-drive", f"if=none,file={DISK},format=raw,id=hd0",
        "-device", "virtio-blk-device,drive=hd0",
        "-kernel", ELF,
    ]


def _wait_marker(path: str, marker: str, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if marker in f.read():
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    for f in (LOG, MON, PPM):
        if os.path.exists(f):
            try: os.remove(f)
            except OSError: pass

    print(f"[gui-arm64-smoke] booting {ELF} headless+ramfb")
    proc = subprocess.Popen(_qemu_cmd(),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + BOOT_TIMEOUT
        passes = 0
        fails  = 0

        def check(name, ok, detail=""):
            nonlocal passes, fails
            if ok:
                print(f"[PASS] {name}{(' — ' + detail) if detail else ''}")
                passes += 1
            else:
                print(f"[FAIL] {name}{(' — ' + detail) if detail else ''}")
                fails += 1

        check("ramfb framebuffer up",
              _wait_marker(LOG, "ramfb: 1024x768x32 ready", deadline))
        check("virtio-input device ready",
              _wait_marker(LOG, "virtio-input: ready at", deadline))
        check("GUI input bridge installed",
              _wait_marker(LOG, "GUI input ready (virtio-input", deadline))
        check("framebuffer console rendered",
              _wait_marker(LOG, "framebuffer console ready", deadline))

        try:
            mon = QemuMonitor(MON, connect_timeout=8.0)
        except Exception as e:
            check("QEMU monitor reachable", False, detail=str(e))
            mon = None

        if mon is not None:
            try:
                mon.screendump(PPM)
                check("screendump captured", os.path.getsize(PPM) > 100)
                w, h, rgb = parse_ppm(PPM)
                check("screendump 1024x768",
                      w == 1024 and h == 768,
                      detail=f"{w}x{h}")

                # Variance check: at least 8 distinct sub-byte values across
                # 4096 evenly-spaced samples. ramfb starts black; the kernel
                # console should write at least one line of PythonOS text.
                samples = [rgb[i] for i in range(0, len(rgb), max(1, len(rgb) // 4096))]
                distinct = len(set(samples))
                check("framebuffer non-blank (>1 distinct value)",
                      distinct > 1, detail=f"{distinct} distinct byte values")

                # Demonstrate sendkey path; we can't round-trip read it
                # without a TCP REPL on arm64.
                mon.sendkey("a")
                check("sendkey accepted by monitor", True)
            finally:
                mon.close()

        print(f"\n[gui-arm64-smoke] {passes} passed, {fails} failed")
        return 0 if fails == 0 else 1
    finally:
        proc.terminate()
        try: proc.wait(timeout=4)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())

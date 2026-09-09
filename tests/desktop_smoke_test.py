#!/usr/bin/env python3
"""
End-to-end desktop smoke: boot the kernel, send `desktop('bouncing_ball')`
over the TCP REPL, screendump, and verify the
compositor-rendered desktop is visible with three signature pixels:

    desktop background  (11, 17, 35)   = bundled background at (30, 30)
    title-bar chrome    (34, 68, 136)  = 0x224488 (focused)
    bouncing_ball body  (16, 24, 32)   = 0x101820

This is the public desktop() happy path — the same guest launcher users can
call after boot, just with `-display none` and a monitor socket so
we can screendump for assertions.
"""

import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qmp_helper import (
    QemuMonitor, parse_ppm, sample_pixel, color_close,
    tile_hashes, golden_check_or_refresh,
)


ISO = sys.argv[1] if len(sys.argv) > 1 else "build/pythonos.iso"
PORT = int(os.environ.get("PYTHONOS_GUI_HOST_PORT", "5560"))
MON  = "/tmp/pythonos-desktop.mon.sock"
PPM  = "/tmp/pythonos-desktop.ppm"
LOG  = "/tmp/pythonos-desktop.log"
BOOT_TIMEOUT = float(os.environ.get("PYTHONOS_GUI_BOOT_TIMEOUT", "30"))


def _qemu_cmd():
    return [
        "qemu-system-x86_64",
        "-machine", "q35",
        "-cpu", "qemu64",
        "-m", "2G",
        "-smp", "2",
        "-netdev", f"user,id=net0,hostfwd=tcp::{PORT}-:5000",
        "-device", "virtio-net-pci,netdev=net0",
        "-no-reboot", "-no-shutdown",
        "-cdrom", ISO,
        "-boot", "d",
        "-display", "none",
        "-vga", "std",
        "-serial", f"file:{LOG}",
        "-monitor", f"unix:{MON},server,nowait",
    ]


def main() -> int:
    for f in (LOG, MON, PPM):
        if os.path.exists(f):
            try: os.remove(f)
            except OSError: pass

    print(f"[desktop-smoke] booting {ISO} on TCP {PORT}")
    proc = subprocess.Popen(_qemu_cmd(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # Wait for REPL.
        deadline = time.time() + BOOT_TIMEOUT
        s = None
        while time.time() < deadline and s is None:
            try:
                s = socket.create_connection(("localhost", PORT), timeout=2)
            except OSError:
                time.sleep(0.5)
        if s is None:
            print("[desktop-smoke] kernel REPL never came up", file=sys.stderr)
            return 2

        s.settimeout(4)
        # Wait for the shell to finish writing its banner so our command
        # actually lands at a prompt rather than mid-banner.
        for _ in range(30):
            time.sleep(0.5)
            try:
                s.sendall(b"\n")
                d = s.recv(4096)
                if b">>>" in d:
                    break
            except (TimeoutError, BlockingIOError, OSError):
                continue

        # Auto-launch the desktop through the public REPL helper.
        s.sendall(b"desktop('bouncing_ball')\n")
        print("[desktop-smoke] sent: desktop('bouncing_ball')")

        mon = QemuMonitor(MON, connect_timeout=5)
        try:
            # Desktop startup imports the app registry and decodes the
            # background in the guest.  Under TCG that takes much longer than
            # under KVM, so wait for the window's signature pixels instead of
            # assuming a fixed four-second startup time.
            frame_deadline = time.time() + 60.0
            w = h = 0
            rgb = b""
            while time.time() < frame_deadline:
                mon.screendump(PPM)
                w, h, rgb = parse_ppm(PPM)
                if w == 1024 and h == 768:
                    title = sample_pixel(w, rgb, 80 + 160, 80 + 8)
                    body = sample_pixel(w, rgb, 80 + 160,
                                        80 + 22 + 100)
                    if (color_close(title, (0x22, 0x44, 0x88), tolerance=8)
                            and color_close(body, (0x10, 0x18, 0x20),
                                            tolerance=8)):
                        break
                time.sleep(0.25)

            # Preserve any shell-side startup error in the test log.  The
            # desktop command is long-running on success, so ordinary output
            # is otherwise intentionally left unread while screenshots run.
            app_output = bytearray()
            s.settimeout(0.1)
            try:
                while True:
                    chunk = s.recv(8192)
                    if not chunk:
                        break
                    app_output.extend(chunk)
            except (TimeoutError, BlockingIOError):
                pass
            if app_output:
                decoded = app_output.decode("utf-8", errors="replace")
                if "Traceback" in decoded or "crashed:" in decoded:
                    print("[desktop-smoke] guest output:\n" + decoded)

            passes = 0
            fails  = 0

            def check(name: str, ok: bool, detail: str = "") -> None:
                nonlocal passes, fails
                if ok:
                    print(f"[PASS] {name}{(' — ' + detail) if detail else ''}")
                    passes += 1
                else:
                    print(f"[FAIL] {name}{(' — ' + detail) if detail else ''}")
                    fails += 1

            check("screendump 1024x768", w == 1024 and h == 768,
                  detail=f"{w}x{h}")

            desk = sample_pixel(w, rgb, 30, 30)
            check("compositor desktop bg",
                  color_close(desk, (0x0B, 0x11, 0x23), tolerance=8),
                  detail=f"rgb={desk}")

            # Default bouncing_ball geometry: x=80, y=80, w=320, h=200, chrome on.
            title = sample_pixel(w, rgb, 80 + 160, 80 + 8)
            check("focused window title bar",
                  color_close(title, (0x22, 0x44, 0x88), tolerance=8),
                  detail=f"rgb={title}")

            body = sample_pixel(w, rgb, 80 + 160, 80 + 22 + 100)
            check("bouncing_ball body bg",
                  color_close(body, (0x10, 0x18, 0x20), tolerance=8),
                  detail=f"rgb={body}")

            # Tile-hash golden — extra coarse check that the overall
            # composition matches what we baselined. The bouncing_ball
            # animation moves ~4 tiles per frame, plus boot text and
            # cursor blink can fluctuate, so we tolerate a generous
            # number of tile mismatches; the goal is to catch regressions
            # like "compositor stopped painting" not pixel-perfect parity.
            ths = tile_hashes(rgb, w, h, tile=16)
            golden_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "goldens", "x86_64", "desktop.tilehashes")
            ok, detail = golden_check_or_refresh(ths, golden_path,
                                                  max_diffs=200)
            check("tile-hash golden check (max_diffs=200)", ok, detail=detail)

            print(f"\n[desktop-smoke] {passes} passed, {fails} failed")
            return 0 if fails == 0 else 1
        finally:
            mon.close()
            s.close()
    finally:
        proc.terminate()
        try: proc.wait(timeout=4)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())

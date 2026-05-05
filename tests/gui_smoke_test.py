#!/usr/bin/env python3
"""
Boot the x86 ISO in headless GUI mode and verify the GUI substrate
came up cleanly.

Boots with `-display none -vga std` so QEMU still emulates the bochs-
VBE adapter (GRUB negotiates a framebuffer through multiboot2) but no
host SDL window opens. Connects to the kernel's TCP REPL and exercises:

    1. The kernel's serial log shows  "framebuffer console ready"
       and "GUI input ready (PS/2)".
    2. `import sdl2` resolves without ImportError.
    3. `examples/sdl_hello.py` runs and prints "sdl_hello: ok".
    4. The `pythonos_gui` command is reachable (validated by listing
       /bin which now contains pythonos_gui.py).

The default `tests/smoke_test.py` is unchanged and remains the gate
for `make test`. This new test runs under `make test-gui-x86_64`.
"""

import os
import socket
import subprocess
import sys
import time

# Allow `python3 tests/gui_smoke_test.py` from the repo root or anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qmp_helper import (
    QemuMonitor, parse_ppm, sample_pixel, color_close,
)

ISO = sys.argv[1] if len(sys.argv) > 1 else "pythonos.iso"
PORT = int(os.environ.get("PYTHONOS_GUI_HOST_PORT", "5559"))
BOOT_TIMEOUT = float(os.environ.get("PYTHONOS_GUI_BOOT_TIMEOUT", "30"))

SERIAL_LOG = "/tmp/pythonos-gui-smoke.log"
MONITOR_SOCK = "/tmp/pythonos-gui-smoke.mon.sock"
SCREENDUMP   = "/tmp/pythonos-gui-smoke.ppm"


def _qemu_cmd():
    return [
        "qemu-system-x86_64",
        "-machine", "q35",
        "-cpu", "qemu64",
        "-m", "2G",
        "-smp", "2",
        "-netdev", f"user,id=net0,hostfwd=tcp::{PORT}-:5000",
        "-device", "virtio-net-pci,netdev=net0",
        "-device", "intel-hda",
        "-device", "hda-duplex",
        "-no-reboot", "-no-shutdown",
        "-cdrom", ISO,
        "-boot", "d",
        "-display", "none",
        "-vga", "std",
        "-serial", f"file:{SERIAL_LOG}",
        "-monitor", f"unix:{MONITOR_SOCK},server,nowait",
    ]


def _connect(deadline: float) -> socket.socket:
    while time.time() < deadline:
        try:
            s = socket.create_connection(("localhost", PORT), timeout=2)
            s.settimeout(8)
            return s
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"GUI smoke: TCP REPL on {PORT} never came up")


def _send(s: socket.socket, line: str, wait: float = 2.5) -> str:
    s.sendall((line + "\n").encode())
    time.sleep(wait)
    chunks = []
    s.settimeout(0.4)
    try:
        while True:
            data = s.recv(8192)
            if not data:
                break
            chunks.append(data)
    except (TimeoutError, BlockingIOError):
        pass
    s.settimeout(8)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _drain(s: socket.socket, wait: float = 1.0) -> None:
    time.sleep(wait)
    s.settimeout(0.3)
    try:
        while True:
            d = s.recv(8192)
            if not d:
                break
    except (TimeoutError, BlockingIOError):
        pass
    s.settimeout(8)


def main() -> int:
    for path in (SERIAL_LOG, MONITOR_SOCK, SCREENDUMP):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    print(f"[gui-smoke] booting {ISO} headless+vga-std on TCP {PORT}")
    proc = subprocess.Popen(_qemu_cmd(),
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + BOOT_TIMEOUT
        s = _connect(deadline)
        # Wait for the prompt by sending a sentinel and watching for ">>> ".
        for _ in range(60):
            time.sleep(0.5)
            try:
                s.sendall(b"\n")
            except OSError:
                break
            try:
                s.settimeout(0.4)
                d = s.recv(4096)
                if b">>>" in d:
                    break
            except (TimeoutError, BlockingIOError):
                continue
            finally:
                s.settimeout(8)
        _drain(s, 0.5)

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

        # 1. Examples sdl_hello runs end-to-end via the sdl2 shim.
        out = _send(s, "run('/examples/sdl_hello.py')", wait=4.5)
        check("examples/sdl_hello.py runs", "sdl_hello: ok" in out,
              detail=out.splitlines()[-1] if out.strip() else "(empty)")

        # 1b. SDL_Renderer corpus item.
        out = _send(s, "run('/examples/sdl_renderer.py')", wait=4.5)
        check("examples/sdl_renderer.py runs", "sdl_renderer: ok" in out,
              detail=out.splitlines()[-1] if out.strip() else "(empty)")

        # 1c. TTF used to render via the bundled bitmap font, but the
        # mirror-SDL refactor now routes TTF through the host bridge
        # process — this kernel-only smoke can't exercise that path
        # because it boots without spawning pythonos_bridge. The
        # bridge round-trip itself is covered by a future bridge-aware
        # smoke; here we just confirm `sdl2.TTF_Init` is importable.
        out = _send(s, "callable(__import__('sdl2').TTF_Init)", wait=2.5)
        check("sdl2.TTF_Init importable",
              "True" in out,
              detail=out.splitlines()[-1] if out.strip() else "(empty)")

        # 1d. PNG decoder corpus item — decode an embedded 16x16 RGBA PNG.
        out = _send(s, "run('/examples/sdl_image.py')", wait=6.0)
        check("examples/sdl_image.py runs (PNG decode)",
              "sdl_image: ok" in out,
              detail=out.splitlines()[-1] if out.strip() else "(empty)")

        # 1e. JPEG decoder corpus item — decode an embedded 8x8 baseline JPEG.
        out = _send(s, "run('/examples/sdl_jpeg.py')", wait=10.0)
        check("examples/sdl_jpeg.py runs (JPEG decode)",
              "sdl_jpeg: ok" in out,
              detail=out.splitlines()[-1] if out.strip() else "(empty)")

        # 2. /bin/pythonos_gui.py is registered.
        out = _send(s, "sh('ls /bin')", wait=3.0)
        check("/bin/pythonos_gui.py present",
              "pythonos_gui" in out,
              detail="present" if "pythonos_gui" in out else "missing")

        # 3. Mixer + sdl2 sdlmixer constants reachable.
        out = _send(s, "__import__('sdl2').MIX_DEFAULT_FREQUENCY", wait=2.5)
        check("sdl2.MIX_DEFAULT_FREQUENCY reachable",
              "44100" in out,
              detail=(out.strip().splitlines()[-1] if out.strip() else ""))

        # 4. Compositor singleton accessible (don't run it — it'd spawn tasks).
        out = _send(s, "type(__import__('kernel.gui.compositor', fromlist=['compositor']).compositor).__name__", wait=2.5)
        check("kernel.gui.compositor.Compositor importable",
              "Compositor" in out,
              detail=(out.strip().splitlines()[-1] if out.strip() else ""))

        # 5. Pixel-level screendump verification via QEMU monitor.
        # We paint a known-color rectangle directly into the framebuffer
        # via the kernel.display.framebuffer.fb singleton, then capture
        # a screendump and sample pixels. The test is independent of any
        # boot text or cursor positioning because it samples coordinates
        # we just wrote into.
        _send(s, "_fb_mod = __import__('kernel.display.framebuffer', fromlist=['fb'])", wait=1.0)
        _send(s, "_fb = _fb_mod.fb", wait=1.0)
        out = _send(s, "(_fb.width, _fb.height)", wait=1.5)
        check("framebuffer accessible",
              "1024" in out and "768" in out,
              detail=(out.strip().splitlines()[-1] if out.strip() else ""))
        # Pure blue: B=0xFF in our XRGB packing (R<<16|G<<8|B → 0x0000FF).
        _send(s, "_fb.fill_rect(100, 100, 80, 80, 0x0000FF)", wait=1.5)

        try:
            mon = QemuMonitor(MONITOR_SOCK, connect_timeout=8.0)
        except Exception as e:
            check("QEMU monitor reachable", False, detail=str(e))
            mon = None

        if mon is not None:
            try:
                mon.screendump(SCREENDUMP)
                check("screendump captured", os.path.getsize(SCREENDUMP) > 100)

                w, h, rgb = parse_ppm(SCREENDUMP)
                check("screendump is 1024x768",
                      w == 1024 and h == 768,
                      detail=f"{w}x{h}")

                # The 80x80 blue rect we just painted lives at (100..179,
                # 100..179). Sample its centre.
                px = sample_pixel(w, rgb, 140, 140)
                check("blue rect centre is blue",
                      color_close(px, (0, 0, 255), tolerance=8),
                      detail=f"rgb={px}")

                # Outside the rect the pixel must NOT be that exact blue.
                px = sample_pixel(w, rgb, 5, 5)
                check("corner pixel is not blue rect",
                      not color_close(px, (0, 0, 255), tolerance=8),
                      detail=f"rgb={px}")

                # Demonstrate sendkey path; we can't easily round-trip
                # observe a key event in this test (no app focused), but
                # the command should at least dispatch without error.
                mon.sendkey("a")
                check("sendkey dispatched", True, detail="(monitor accepted)")

                # Mouse pipeline: read initial pointer, drive mouse_move,
                # confirm the kernel-side pointer moved.
                _send(s, "_gi = __import__('kernel.gui.input', fromlist=['pointer_position'])", wait=1.0)
                out = _send(s, "_gi.pointer_position()", wait=1.5)
                # Format like "(512, 384)"
                start = out.rfind("(")
                end   = out.rfind(")")
                init_xy = None
                if start != -1 and end != -1 and end > start:
                    try:
                        init_xy = tuple(int(t.strip())
                                        for t in out[start+1:end].split(","))
                    except ValueError:
                        init_xy = None
                check("initial pointer_position() shape",
                      init_xy is not None and len(init_xy) == 2,
                      detail=str(init_xy))

                # Terminal app registers when apps.terminal is imported.
                # Use __import__ instead of `import` because the kernel REPL's
                # interactive compile() rejects bare `import X` (pre-existing
                # frozen-Python quirk; the shell's file-execution path handles
                # `import` fine since that goes through PyCF_ALLOW_TOP_LEVEL_AWAIT).
                _send(s, "_t = __import__('apps.terminal', fromlist=['term'])", wait=4.0)
                out = _send(s, "bool(__import__('apps', fromlist=['registry']).registry.get('terminal'))", wait=4.0)
                check("apps.terminal registered",
                      "True" in out,
                      detail=(out.strip().splitlines()[-1] if out.strip() else "(empty)"))

                _send(s, "_e = __import__('apps.editor', fromlist=['edwin'])", wait=4.0)
                out = _send(s, "bool(__import__('apps', fromlist=['registry']).registry.get('editor'))", wait=4.0)
                check("apps.editor registered",
                      "True" in out,
                      detail=(out.strip().splitlines()[-1] if out.strip() else "(empty)"))

                _send(s, "_iv = __import__('apps.image_viewer', fromlist=['viewer'])", wait=3.0)
                _send(s, "_fb_ = __import__('apps.files', fromlist=['browser'])", wait=3.0)
                _send(s, "_at = __import__('apps.demos.audio_tone', fromlist=['main'])", wait=3.0)
                _send(s, "_r = __import__('apps', fromlist=['registry']).registry", wait=2.0)
                out = _send(s, "len(_r.list_apps()) >= 6", wait=2.5)
                check("apps registry populated (>=6 apps)",
                      "True" in out,
                      detail=(out.strip().splitlines()[-1] if out.strip() else "(empty)"))

                # Render a compositor desktop window and verify pixel-by-pixel
                # that the title bar shows up at the expected screen location.
                # CHROME_FOCUS_BG = 0x224488 → (R=0x22, G=0x44, B=0x88) =
                # (34, 68, 136) in PPM ordering.
                _send(s, "_c = __import__('kernel.gui.compositor', fromlist=['compositor','CompositorWindow'])", wait=2.0)
                _send(s, "_w = _c.CompositorWindow('SmokeDesk', x=200, y=150, w=320, h=200)", wait=1.5)
                _send(s, "_c.compositor.add_window(_w)", wait=1.5)
                _send(s, "_c.compositor.start()", wait=1.5)
                # Give the 30Hz draw loop a couple of ticks.
                time.sleep(1.0)

                mon.screendump(SCREENDUMP)
                w, h, rgb = parse_ppm(SCREENDUMP)

                # Title bar runs from y=150 (chrome top) for 16 px; sample the
                # middle of the bar at the window's horizontal centre.
                title_px = sample_pixel(w, rgb, 200 + 320 // 2, 150 + 8)
                check("compositor drew window title bar",
                      color_close(title_px, (0x22, 0x44, 0x88), tolerance=8),
                      detail=f"rgb={title_px} at ({200 + 160},{158})")

                # Window body interior (below title bar) — initial surface is
                # all-zeros (black). Sample mid-body.
                body_px = sample_pixel(w, rgb, 200 + 320 // 2, 150 + 16 + 100)
                check("compositor drew window body",
                      color_close(body_px, (0, 0, 0), tolerance=8),
                      detail=f"rgb={body_px}")

                # Stop the compositor so we don't leak tasks into later tests.
                _send(s, "import asyncio; asyncio.ensure_future(_c.compositor.stop())", wait=1.0)

                if init_xy is not None:
                    mon.mouse_move(120, 0)
                    time.sleep(1.2)
                    out = _send(s, "_gi.pointer_position()", wait=1.5)
                    s2 = out.rfind("("); e2 = out.rfind(")")
                    new_xy = None
                    if s2 != -1 and e2 != -1 and e2 > s2:
                        try:
                            new_xy = tuple(int(t.strip())
                                           for t in out[s2+1:e2].split(","))
                        except ValueError:
                            new_xy = None
                    check("mouse_move moved kernel pointer",
                          new_xy is not None
                          and (new_xy[0] != init_xy[0] or new_xy[1] != init_xy[1]),
                          detail=f"{init_xy} -> {new_xy}")
            finally:
                mon.close()

        # Inspect serial log markers.
        try:
            with open(SERIAL_LOG, "r", encoding="utf-8", errors="replace") as f:
                serial = f.read()
        except OSError:
            serial = ""

        check("serial: framebuffer console ready",
              "framebuffer console ready" in serial)
        check("serial: GUI input ready (PS/2)",
              "GUI input ready (PS/2" in serial)

        s.close()
        print(f"\n[gui-smoke] {passes} passed, {fails} failed")
        return 0 if fails == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())

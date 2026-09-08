#!/usr/bin/env python3
"""Host-side chipset tests. No QEMU, no _hal.

Run: python3 tests/chipset_test.py
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# kernel/__init__.py is the boot path and imports _hal. Host tests
# register a namespace package so kernel.chipset loads without booting.
import types
if "kernel" not in sys.modules:
    _kernel_pkg = types.ModuleType("kernel")
    _kernel_pkg.__path__ = [os.path.join(ROOT, "kernel")]
    _kernel_pkg.__package__ = "kernel"
    sys.modules["kernel"] = _kernel_pkg

_failed = 0
_passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global _failed, _passed
    ok = bool(cond)
    if ok:
        _passed += 1
        print(f"  PASS  {name}" + (f" ({detail})" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _xrgb_at(buf: bytes, width: int, x: int, y: int) -> int:
    o = (y * width + x) * 4
    b, g, r = buf[o], buf[o + 1], buf[o + 2]
    return (r << 16) | (g << 8) | b


def main() -> int:
    print("chipset_test")

    from kernel.chipset import (
        MODE_DIRECT,
        MODE_INDEXED,
        Move,
        Wait,
        View,
        blitter,
        chipset,
        paula,
    )
    from kernel.chipset.toaster import wipe_step

    # load_view(None) raises
    raised = False
    try:
        chipset.load_view(None)
    except ValueError:
        raised = True
    check("load_view(None) raises ValueError", raised)

    # Tiny dest so goldens stay small
    chipset.set_dest(32, 24)
    chipset.set_present(None)
    chipset.set_mixer(None)

    # Indexed copper: COLOR00 changes at line 8
    v = View(32, 24, mode=MODE_INDEXED, scale=1)
    v.palette[0] = 0x0000FF
    v.palette[1] = 0x00FF00
    v.copper.instructions = [
        Wait(0),
        Move("COLOR00", 0x0000FF),
        Wait(8),
        Move("COLOR00", 0xFF0000),
    ]
    v.pf0.fill(0)
    chipset.load_view(v)
    buf = chipset.tick()
    check("tick dest size 32x24x4", len(buf) == 32 * 24 * 4, str(len(buf)))
    check("copper COLOR00 before wait line",
          _xrgb_at(buf, 32, 0, 2) == 0x0000FF,
          hex(_xrgb_at(buf, 32, 0, 2)))
    check("copper COLOR00 after wait line",
          _xrgb_at(buf, 32, 0, 16) == 0xFF0000,
          hex(_xrgb_at(buf, 32, 0, 16)))

    # Unknown MOVE does not raise
    v.copper.instructions = [Wait(0), Move("NO_SUCH_REG", 1)]
    chipset.load_view(v)
    try:
        chipset.tick()
        check("unknown copper MOVE continues", True)
    except Exception as e:
        check("unknown copper MOVE continues", False, str(e))

    # Blitter fill + clip
    pf = v.pf0
    pf.fill(0)
    blitter.fill(pf, 30, 20, 10, 10, 1)
    check("blitter fill clips x", pf.get(31, 20) == 1)
    check("blitter fill clipped out of bounds", pf.get(0, 0) == 0)

    # Cookie-cut: mask non-zero copies src
    src = v.pf0
    src.fill(0)
    src.put(2, 2, 5)
    src.put(3, 2, 5)
    mask = View(32, 24, mode=MODE_INDEXED).pf0
    mask.fill(0)
    mask.put(2, 2, 1)
    dest = View(32, 24, mode=MODE_INDEXED).pf0
    dest.fill(9)
    blitter.cookie(src, mask, dest, 0, 0, 0, 0, 8, 8)
    check("cookie writes where mask set", dest.get(2, 2) == 5)
    check("cookie leaves dest where mask clear", dest.get(3, 2) == 9)

    # Sprite key: INDEXED index 0 is transparent
    v2 = View(32, 24, mode=MODE_INDEXED, scale=1)
    v2.palette[0] = 0x111111
    v2.palette[2] = 0x00FF00
    v2.pf0.fill(0)
    spr = bytes([0, 2, 2, 0])
    v2.sprites[0].place(spr, w=2, h=2, x=4, y=4, key_color=0)
    chipset.load_view(v2)
    buf = chipset.tick()
    check("sprite opaque pixel",
          _xrgb_at(buf, 32, 5, 4) == 0x00FF00,
          hex(_xrgb_at(buf, 32, 5, 4)))
    check("sprite key shows playfield",
          _xrgb_at(buf, 32, 4, 4) == 0x111111,
          hex(_xrgb_at(buf, 32, 4, 4)))

    # DIRECT playfield pixel
    vd = View(16, 16, mode=MODE_DIRECT, scale=1)
    chipset.set_dest(16, 16)
    vd.pf0.fill(0x102040)
    chipset.load_view(vd)
    buf = chipset.tick()
    check("direct pf0 pixel",
          _xrgb_at(buf, 16, 1, 1) == 0x102040,
          hex(_xrgb_at(buf, 16, 1, 1)))

    # Letterbox scale 2: 8x8 view on 16x16 dest
    vs = View(8, 8, mode=MODE_DIRECT, scale=2)
    chipset.set_dest(16, 16)
    vs.pf0.fill(0xAABBCC)
    chipset.load_view(vs)
    buf = chipset.tick()
    check("scaled view maps origin",
          _xrgb_at(buf, 16, 0, 0) == 0xAABBCC,
          hex(_xrgb_at(buf, 16, 0, 0)))

    # Paula mix
    square = bytearray()
    for i in range(64):
        s = 12000 if (i % 8) < 4 else -12000
        square += struct.pack("<h", s)
    paula.channel[0].sample = bytes(square)
    paula.channel[0].rate = 8000
    paula.channel[0].volume = 64
    paula.channel[0].pan = 0
    paula.channel[0].loop = True
    paula.channel[0].loop_start = 0
    paula.channel[0].loop_end = 64
    paula.channel[0].play()
    paula.channel[1].sample = bytes(square)
    paula.channel[1].rate = 8000
    paula.channel[1].volume = 64
    paula.channel[1].pan = 255
    paula.channel[1].loop = True
    paula.channel[1].loop_start = 0
    paula.channel[1].loop_end = 64
    paula.channel[1].play()
    mixed = paula.mix(1600)
    check("paula mix length", len(mixed) == 1600 * 4, str(len(mixed)))
    check("paula mix not silence", any(b != 0 for b in mixed))
    samples = struct.unpack("<" + "h" * (len(mixed) // 2), mixed)
    left = sum(abs(samples[i]) for i in range(0, len(samples), 2))
    right = sum(abs(samples[i]) for i in range(1, len(samples), 2))
    check("paula pan splits energy", left > 0 and right > 0)
    paula.channel[0].volume = 0
    paula.channel[1].volume = 0
    silent = paula.mix(100)
    check("paula volume 0 is silence", all(b == 0 for b in silent))
    paula.channel[0].stop()
    paula.channel[1].stop()

    # Wipe helper
    tw = View(64, 40, mode=MODE_DIRECT, scale=1)
    tw.diw_start = 0
    tw.diw_stop = 0
    wipe_step(tw, 0, 10)
    start0 = tw.diw_stop
    wipe_step(tw, 10, 10)
    check("wipe_step advances DIW", tw.diw_stop > start0,
          f"{start0} -> {tw.diw_stop}")

    from kernel.chipset.clock import TICK_HZ
    check("clock TICK_HZ is 30", TICK_HZ == 30)

    # Workbench-style DIRECT 1:1 dest keeps a chrome strip (fast path)
    chipset.set_dest(16, 16)
    wb = View(16, 16, mode=MODE_DIRECT, scale=1)
    wb.pf0.fill(0x202840)
    for x in range(16):
        wb.pf0.put(x, 0, 0x1B1F2A)
        wb.pf0.put(x, 15, 0x14182A)
    chipset.load_view(wb)
    buf = chipset.tick()
    check("workbench menubar row survives raster",
          _xrgb_at(buf, 16, 8, 0) == 0x1B1F2A,
          hex(_xrgb_at(buf, 16, 8, 0)))
    check("workbench dock row survives raster",
          _xrgb_at(buf, 16, 8, 15) == 0x14182A,
          hex(_xrgb_at(buf, 16, 8, 15)))

    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())

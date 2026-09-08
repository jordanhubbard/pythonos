"""apps.toaster — Video Toaster studio View (dual playfields + wipe)."""

from __future__ import annotations

import asyncio

from kernel.chipset import MODE_DIRECT, View, chipset, paula
from kernel.chipset.toaster import wipe_step
from kernel.gui import input as _gui_input
from apps import registry


def _fill_bars(pf, width: int, height: int) -> None:
    colors = (0xC00000, 0xC0C000, 0x00C000, 0x00C0C0,
              0x0000C0, 0xC000C0, 0xC0C0C0, 0x202020)
    band = max(1, width // len(colors))
    for i, color in enumerate(colors):
        x0 = i * band
        x1 = width if i == len(colors) - 1 else (i + 1) * band
        for y in range(height):
            for x in range(x0, x1):
                pf.put(x, y, color)


def _fill_plasma(pf, width: int, height: int, t: int) -> None:
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r = (x * 3 + t) & 0xFF
            g = (y * 5 + t * 2) & 0xFF
            b = ((x + y + t) * 7) & 0xFF
            color = (r << 16) | (g << 8) | b
            pf.put(x, y, color)
            pf.put(x + 1, y, color)
            pf.put(x, y + 1, color)
            pf.put(x + 1, y + 1, color)


async def main(*args, **kwargs) -> None:
    v = View(320, 200, mode=MODE_DIRECT, scale=3)
    _fill_bars(v.pf0, 320, 200)
    v.pf1.fill(0)
    v.bplcon = 1  # PF1 key
    v.key_color = 0
    v.diw_start = 0
    v.diw_stop = v.height - 1

    prev = chipset.active_view
    chipset.load_view(v)

    wiping = False
    wipe_t = 0.0
    program_b = False
    closed = False
    tick = 0

    def on_event(ev):
        nonlocal closed, wiping, wipe_t, program_b
        if ev.kind != _gui_input.KEY_DOWN:
            return
        if ev.code == _gui_input.KEY_ESC:
            closed = True
        elif ev.text in ("w", "W") or ev.code in (ord("w"), ord("W")):
            wiping = True
            wipe_t = 0.0
            stinger = paula.channel[1]
            stinger.sample = paula.channel[0].sample or b""
            if not stinger.sample:
                import struct
                buf = bytearray()
                for i in range(800):
                    s = 8000 if (i % 10) < 5 else -8000
                    buf += struct.pack("<h", s)
                stinger.sample = bytes(buf)
            stinger.rate = 8000
            stinger.volume = 40
            stinger.pan = 128
            stinger.loop = False
            stinger.play()
        elif ev.text == "1" or ev.code == ord("1"):
            program_b = False
            v.diw_stop = v.height - 1
        elif ev.text == "2" or ev.code == ord("2"):
            program_b = True

    chipset.on_event = on_event

    while not closed:
        if program_b and not wiping:
            v.bplcon = 0
        elif wiping:
            if wipe_t <= 0.0:
                _fill_plasma(v.pf1, 320, 200, tick)
            wipe_t += 1.0
            wipe_step(v, wipe_t, 30.0)
            v.bplcon = 0
            if wipe_t >= 30.0:
                wiping = False
                program_b = True
                v.diw_stop = v.height - 1
        else:
            v.bplcon = 1
        tick += 4
        await asyncio.sleep(1.0 / 30)

    paula.channel[1].stop()
    chipset.on_event = None
    if chipset.workbench is not None:
        chipset.load_view(chipset.workbench)
    elif prev is not None:
        chipset.load_view(prev)


from apps._icons import toaster_icon

registry.register(
    name="toaster",
    description="Video Toaster — dual playfields, wipe, Paula stinger",
    entry=main,
    icon_factory=toaster_icon,
    category="app",
)

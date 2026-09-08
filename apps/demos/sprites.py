"""apps.demos.sprites — LoadView sprite/copper/Paula game screen."""

from __future__ import annotations

import asyncio
import struct

from kernel.chipset import (
    MODE_INDEXED,
    Move,
    View,
    Wait,
    chipset,
    paula,
)
from kernel.gui import input as _gui_input
from apps import registry


def _square(freq: int, ms: int, rate: int = 8000) -> bytes:
    n = rate * ms // 1000
    half = max(1, rate // (freq * 2))
    out = bytearray()
    for i in range(n):
        s = 10000 if (i // half) % 2 == 0 else -10000
        out += struct.pack("<h", s)
    return bytes(out)


def _ship_art() -> bytes:
    # 8x8 INDEXED: 0 = key, 2 = body
    rows = [
        "00022000",
        "00222200",
        "02222220",
        "22222222",
        "00222200",
        "00222200",
        "02000020",
        "20000002",
    ]
    out = bytearray()
    for row in rows:
        for ch in row:
            out.append(2 if ch == "2" else 0)
    return bytes(out)


def _enemy_art() -> bytes:
    rows = [
        "00333300",
        "03333330",
        "33000033",
        "33333333",
        "03333330",
        "03000030",
        "00300300",
        "00000000",
    ]
    out = bytearray()
    for row in rows:
        for ch in row:
            out.append(3 if ch == "3" else 0)
    return bytes(out)


async def main(*args, **kwargs) -> None:
    v = View(320, 200, mode=MODE_INDEXED, scale=3)
    v.palette[0] = 0x081028
    v.palette[1] = 0x204060
    v.palette[2] = 0xFFCC00
    v.palette[3] = 0xFF4060
    v.copper.instructions = [
        Wait(0), Move("COLOR00", 0x081028),
        Wait(40), Move("COLOR00", 0x102040),
        Wait(120), Move("COLOR00", 0x301018),
    ]
    v.pf0.fill(0)
    v.sprites[0].place(_ship_art(), 8, 8, x=156, y=160, key_color=0)
    v.sprites[1].place(_enemy_art(), 8, 8, x=40, y=30, key_color=0)
    v.sprites[2].place(_enemy_art(), 8, 8, x=200, y=50, key_color=0)
    shot = paula.channel[1]
    shot.sample = _square(880, 80)
    shot.rate = 8000
    shot.volume = 36
    shot.pan = 128
    shot.loop = False

    bass = _square(110, 400)
    paula.channel[0].sample = bass
    paula.channel[0].rate = 8000
    paula.channel[0].volume = 24
    paula.channel[0].pan = 128
    paula.channel[0].loop = True
    paula.channel[0].loop_end = len(bass) // 2
    paula.channel[0].play()

    prev = chipset.active_view
    chipset.load_view(v)

    keys = set()
    closed = False
    missile = v.sprites[3]
    missile.enabled = False

    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN:
            if ev.code == _gui_input.KEY_ESC:
                closed = True
            elif ev.code == _gui_input.KEY_SPACE:
                if not missile.enabled:
                    missile.place(bytes([2, 2, 2, 2]), 1, 4,
                                  x=v.sprites[0].x + 3,
                                  y=v.sprites[0].y - 6, key_color=0)
                    shot.stop()
                    shot.play()
            keys.add(ev.code)
        elif ev.kind == _gui_input.KEY_UP:
            keys.discard(ev.code)

    chipset.on_event = on_event
    ship = v.sprites[0]
    while not closed:
        if _gui_input.KEY_LEFT in keys:
            ship.x = max(0, ship.x - 4)
        if _gui_input.KEY_RIGHT in keys:
            ship.x = min(312, ship.x + 4)
        if _gui_input.KEY_UP in keys:
            ship.y = max(0, ship.y - 4)
        if _gui_input.KEY_DOWN in keys:
            ship.y = min(192, ship.y + 4)
        if missile.enabled:
            missile.y -= 8
            if missile.y < 0:
                missile.enabled = False
        v.sprites[1].x = (v.sprites[1].x + 2) % 312
        v.sprites[2].x = (v.sprites[2].x - 1) % 312
        await asyncio.sleep(1.0 / 30)

    paula.channel[0].stop()
    shot.stop()
    chipset.on_event = None
    if chipset.workbench is not None:
        chipset.load_view(chipset.workbench)
    elif prev is not None:
        chipset.load_view(prev)


from apps._icons import bouncing_ball_icon

registry.register(
    name="sprites",
    description="Chipset sprite / copper / Paula demo",
    entry=main,
    icon_factory=bouncing_ball_icon,
    category="demo",
)

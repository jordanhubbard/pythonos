"""Lesson 1: an indexed playfield, palette, View, and scroll register."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:5] = [0x081020, 0x204080, 0x40A0D0, 0x80D0A0, 0xFFE080]
    for y in range(view.height):
        for x in range(view.width):
            view.pf0.put(x, y, 1 + ((x // 24 + y // 24) % 4))

    def tick(keys):
        view.pf0.scroll_x = (view.pf0.scroll_x + 1) % view.width

    await run_view(view, tick, controls="PLAYFIELD AUTO-SCROLL")

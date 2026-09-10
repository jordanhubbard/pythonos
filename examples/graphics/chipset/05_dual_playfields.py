"""Lesson 5: a keyed foreground playfield scrolls over a background one."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View, blitter


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:6] = [0x000000, 0x102850, 0x205090, 0x50C080, 0xE0D060, 0xF08050]
    view.key_color = 0
    view.bplcon = 1
    for y in range(view.height):
        for x in range(view.width):
            view.pf0.put(x, y, 1 + ((x // 32 + y // 24) & 1))
    view.pf1.fill(0)
    for x in range(0, 320, 70):
        blitter.fill(view.pf1, x, 50 + (x % 90), 34, 28, 3 + (x // 70) % 3)

    def tick(keys):
        view.pf0.scroll_x = (view.pf0.scroll_x + 1) % 320
        view.pf1.scroll_x = (view.pf1.scroll_x - 2) % 320

    await run_view(view, tick, controls="DUAL PLAYFIELD PARALLAX")

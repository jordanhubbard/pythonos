"""Lesson 7: DIWSTART/DIWSTOP reveal a bounded raster display window."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:5] = [0x081020, 0xE05050, 0xE0B040, 0x50B080, 0x5090E0]
    for y in range(view.height):
        for x in range(view.width):
            view.pf0.put(x, y, 1 + (y // 24) % 4)
    frame = 0

    def tick(keys):
        nonlocal frame
        edge = 20 + abs((frame % 140) - 70)
        view.diw_start = edge
        view.diw_stop = view.height - edge
        frame += 1

    await run_view(view, tick, controls="DISPLAY WINDOW / RASTER")

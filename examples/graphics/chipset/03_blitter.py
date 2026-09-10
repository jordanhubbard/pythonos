"""Lesson 3: the Blitter fills and copies rectangular bitmap regions."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View, blitter


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:5] = [0x081018, 0x40D080, 0x60A0FF, 0xFFB040, 0xF05070]
    frame = 0

    def tick(keys):
        nonlocal frame
        view.pf0.fill(0)
        x = (frame * 3) % 300 - 20
        blitter.fill(view.pf0, x, 35, 48, 34, 1)
        blitter.fill(view.pf0, 250 - x, 90, 56, 42, 2)
        blitter.fill(view.pf0, 120, 145, 80, 24, 3)
        blitter.copy(view.pf0, view.pf0, 120, 145, 30, 110, 80, 24)
        frame += 1

    await run_view(view, tick, controls="BLITTER FILL / COPY")

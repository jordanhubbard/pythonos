"""Lesson 2: the Copper changes palette registers at raster scanlines."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, Move, View, Wait


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.pf0.fill(0)
    view.copper.instructions = [
        Wait(0), Move("COLOR00", 0x102050),
        Wait(40), Move("COLOR00", 0x3060A0),
        Wait(80), Move("COLOR00", 0x60A0C0),
        Wait(120), Move("COLOR00", 0xE09050),
        Wait(160), Move("COLOR00", 0x401830),
    ]
    await run_view(view, lambda keys: None, controls="COPPER SCANLINE BANDS")

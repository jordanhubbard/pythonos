"""Lesson 4: transparent sprite channels move over an untouched playfield."""

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View


_SPRITE = bytes(int(ch) for row in (
    "00111100", "01111110", "11011011", "11111111",
    "01111110", "01011010", "11000011", "00000000") for ch in row)


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:4] = [0x081020, 0x203050, 0xFFFFFF, 0x50F080]
    for y in range(view.height):
        for x in range(view.width):
            view.pf0.put(x, y, 1 if (x + y) % 31 else 2)
    sprite = view.sprites[0]
    sprite.place(bytes(3 if value else 0 for value in _SPRITE), 8, 8,
                 x=10, y=80, key_color=0)
    dx = 2

    def tick(keys):
        nonlocal dx
        sprite.x += dx
        if sprite.x <= 0 or sprite.x >= view.width - sprite.w:
            dx = -dx

    await run_view(view, tick, controls="SPRITE OVER PLAYFIELD")

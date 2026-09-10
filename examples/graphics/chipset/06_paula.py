"""Lesson 6: four Paula PCM voices demonstrate pitch, looping, and pan."""

import struct

from apps.chipset_play import run_view
from kernel.chipset import MODE_INDEXED, View, blitter, paula


def square(freq, milliseconds=240, rate=8000):
    frames = rate * milliseconds // 1000
    half = max(1, rate // (freq * 2))
    return b"".join(struct.pack("<h", 9000 if (i // half) % 2 else -9000)
                    for i in range(frames))


async def main(argv=None):
    view = View(320, 200, mode=MODE_INDEXED, scale=3)
    view.palette[:6] = [0x081020, 0x40D080, 0x60A0FF, 0xFFB040, 0xF06080, 0xFFFFFF]
    view.pf0.fill(0)
    for number, channel in enumerate(paula.channel):
        channel.sample = square((110, 165, 220, 330)[number])
        channel.rate = 8000
        channel.volume = 22
        channel.pan = (0, 85, 170, 255)[number]
        channel.loop = True
        channel.loop_end = len(channel.sample) // 2
        channel.play()

    def tick(keys):
        for number, channel in enumerate(paula.channel):
            height = 24 + int(channel._pos) % 100
            x = 28 + number * 72
            blitter.fill(view.pf0, x, 20, 44, 150, 0)
            blitter.fill(view.pf0, x, 170 - height, 44, height, number + 1)

    def stop():
        for channel in paula.channel:
            channel.stop()

    await run_view(view, tick, on_exit=stop, controls="PAULA FOUR-VOICE STEREO")

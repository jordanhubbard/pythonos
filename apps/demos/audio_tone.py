"""apps.demos.audio_tone — Plays a 440 Hz square-wave tone via the mixer.

Opens a small status window, generates a half-second tone using the
same square-wave generator as ``examples/tone.py``, then pushes it
through :class:`kernel.sound.mixer.Mixer.play_pcm`. ESC closes.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect
from kernel.sound.mixer import mixer
from apps import registry


_SAMPLE_RATE = 48000
_CHANNELS = 2
_TONE_MS = 500
_FREQ = 440
_BG = 0x101820
_FG = 0xFFCC00


def _square_tone(freq: int, ms: int) -> bytes:
    frames = _SAMPLE_RATE * ms // 1000
    half_period = max(1, _SAMPLE_RATE // (freq * 2))
    out = bytearray(frames * _CHANNELS * 2)
    for i in range(frames):
        sample = 12000 if (i // half_period) % 2 == 0 else -12000
        lo =  sample        & 0xFF
        hi = (sample >> 8) & 0xFF
        off = i * 4
        out[off]     = lo
        out[off + 1] = hi
        out[off + 2] = lo
        out[off + 3] = hi
    return bytes(out)


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Audio Tone", x=140, y=140, w=300, h=120)
    compositor.add_window(win)
    SDL_FillRect(win.surface, None, _BG)
    win.surface.draw_text(8,  20, "440 Hz tone, 0.5s",          fg=_FG, bg=_BG)
    win.surface.draw_text(8,  44, "playing through mixer...",   fg=_FG, bg=_BG)
    win.surface.draw_text(8,  88, "ESC to close",               fg=_FG, bg=_BG)
    win.dirty = True

    pcm = _square_tone(_FREQ, _TONE_MS)
    consumed = mixer.play_pcm(pcm, channels=_CHANNELS, fmt="int16")

    win.surface.draw_text(8, 64, "consumed: " + str(consumed) + " bytes",
                           fg=_FG, bg=_BG)
    win.dirty = True

    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True
    win.set_event_handler(on_event)

    while not closed and not win._closed:
        await asyncio.sleep(0.1)
    win.close()


from apps._icons import audio_tone_icon

registry.register(
    name="audio_tone",
    description="440 Hz tone through the mixer (audio demo)",
    entry=main,
    icon_factory=audio_tone_icon,
    category="demo",
)

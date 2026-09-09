"""apps.demos.rainfall — Falling rain streaks in a compositor window.

Each drop is a vertical FillRect of 1 px wide × `length` px tall, falling
at its own speed, respawning at the top once it leaves the bottom edge.
A faint "puddle" splash flickers at the bottom row when drops land.
Pure SDL_FillRect, friendly to the bridge.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import _new_icon, _border


_BG      = 0x101218
_WIDTH   = 360
_HEIGHT  = 280
_NUM     = 90
_PUDDLE  = 0x3060A0


def _seed(seed: int = 0xBADCAB):
    state = [seed]

    def randint(lo: int, hi: int) -> int:
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return lo + state[0] % max(1, hi - lo)

    return randint


def _spawn_drop(rand, above: bool):
    # (x, y, length, speed, color)
    x = rand(0, _WIDTH)
    y = rand(-_HEIGHT, 0) if above else rand(0, _HEIGHT)
    length = rand(6, 18)
    speed = rand(4, 11)
    # Three rain shades, deeper for slower drops (parallax cue).
    shade = 0x4080FF if speed > 8 else (0x3060C0 if speed > 6 else 0x204080)
    return [x, y, length, speed, shade]


async def _run(win: CompositorWindow) -> None:
    rand = _seed()
    drops = [_spawn_drop(rand, above=False) for _ in range(_NUM)]

    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True

    win.set_event_handler(on_event)

    s = win.surface
    splash_x = []   # x positions where drops landed this frame
    while not closed and not win._closed:
        SDL_FillRect(s, None, _BG)

        # Bottom puddle row + recent splashes.
        SDL_FillRect(s, SDL_Rect(0, _HEIGHT - 2, _WIDTH, 2), 0x202830)
        for x in splash_x:
            SDL_FillRect(s, SDL_Rect(max(0, x - 2), _HEIGHT - 4, 5, 1), _PUDDLE)
        splash_x = []

        # Step + draw each drop.
        for d in drops:
            d[1] += d[3]
            if d[1] >= _HEIGHT:
                splash_x.append(d[0])
                nd = _spawn_drop(rand, above=True)
                d[0], d[1], d[2], d[3], d[4] = nd
                continue
            top = max(0, d[1] - d[2])
            height = d[1] - top
            if height > 0:
                SDL_FillRect(s, SDL_Rect(d[0], top, 1, height), d[4])

        win.dirty = True
        await asyncio.sleep(1.0 / 30)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Rainfall", x=140, y=110,
                            w=_WIDTH, h=_HEIGHT)
    compositor.add_window(win)
    await _run(win)


def rainfall_icon():
    s = _new_icon(0x101218)
    _border(s, 0x303848)
    # Three diagonal rain streaks.
    for x, y in ((10, 6), (22, 12), (34, 4), (14, 24), (28, 28), (40, 18)):
        SDL_FillRect(s, SDL_Rect(x, y, 1, 8), 0x4080FF)
    # Puddle line at the bottom.
    SDL_FillRect(s, SDL_Rect(2, 42, 44, 2), 0x3060A0)
    return s


registry.register(
    name="rainfall",
    description="Falling rain streaks (graphics demo)",
    entry=main,
    icon_factory=rainfall_icon,
    category="demo",
)

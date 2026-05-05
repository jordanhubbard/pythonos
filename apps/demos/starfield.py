"""apps.demos.starfield — Hyperspace starfield in a compositor window.

3D stars with depth-based perspective: each star has (x, y, z) in a
camera-space cube; we project to (sx, sy) on screen and grow + brighten
the rendered rect as z shrinks toward the camera. When a star passes
through the near plane, it respawns at max-z with fresh random (x, y).

Pure SDL_FillRect drawing — no per-pixel work — so the per-frame cost
is one bridge op per star.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import _new_icon, _border


_BG       = 0x000010
_WIDTH    = 480
_HEIGHT   = 320
_NUM      = 140
_SPEED    = 0.012     # fraction of z consumed per frame
_FOV      = 320       # focal length in pixels


def _seed(seed: int = 0xC0FFEE) -> "callable":
    """Tiny LCG so the demo is deterministic and doesn't depend on
    bare-metal random availability."""
    state = [seed]

    def rand_unit() -> float:
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return state[0] / 0x7FFFFFFF

    return rand_unit


def _spawn(rand) -> list:
    # x/y in [-1, 1], z in (0, 1] (camera near=0, far=1).
    return [rand() * 2 - 1, rand() * 2 - 1, rand()]


async def _run(win: CompositorWindow) -> None:
    rand = _seed()
    stars = [_spawn(rand) for _ in range(_NUM)]
    cx, cy = _WIDTH // 2, _HEIGHT // 2

    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True

    win.set_event_handler(on_event)

    s = win.surface
    while not closed and not win._closed:
        SDL_FillRect(s, None, _BG)

        for star in stars:
            x, y, z = star
            z -= _SPEED
            if z <= 0.001:
                # Respawn at the far plane with new x/y.
                ns = _spawn(rand)
                ns[2] = 1.0
                star[0], star[1], star[2] = ns
                continue
            star[2] = z

            # Perspective projection: 1/z grows as the star approaches.
            inv = 1.0 / z
            sx = int(cx + x * _FOV * inv)
            sy = int(cy + y * _FOV * inv)
            if 0 <= sx < _WIDTH and 0 <= sy < _HEIGHT:
                # Size + brightness scale with proximity. Clamp to 4 px.
                size = 1 + int(min(3, (1 - z) * 4))
                bright = int(80 + (1 - z) * 175)   # 80..255
                color = (bright << 16) | (bright << 8) | bright
                SDL_FillRect(s, SDL_Rect(sx, sy, size, size), color)

        win.dirty = True
        await asyncio.sleep(1.0 / 30)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Starfield", x=110, y=80,
                            w=_WIDTH, h=_HEIGHT)
    compositor.add_window(win)
    await _run(win)


def starfield_icon():
    s = _new_icon(0x000010)
    _border(s, 0x303060)
    # A few "stars" of varying sizes.
    for sx, sy, size, c in (
        (10, 12, 1, 0x808080),
        (22, 8,  2, 0xFFFFFF),
        (35, 18, 1, 0xA0A0A0),
        (16, 28, 3, 0xFFFFFF),
        (30, 35, 2, 0xC0C0C0),
        (38, 30, 1, 0x808080),
    ):
        SDL_FillRect(s, SDL_Rect(sx, sy, size, size), c)
    return s


registry.register(
    name="starfield",
    description="Hyperspace starfield (3D perspective demo)",
    entry=main,
    icon_factory=starfield_icon,
    category="demo",
)

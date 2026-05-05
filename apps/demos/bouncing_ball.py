"""apps.demos.bouncing_ball — A bouncing rectangle in a compositor window.

Self-contained demo: opens a 320x200 window, animates a rectangle
bouncing off the walls at ~30 Hz. ESC closes the window.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect, SDL_MapRGB
from apps import registry


_BG    = 0x101820
_BALL  = 0xFFCC00
_WIDTH = 320
_HEIGHT = 200
_BALL_SIZE = 24


async def _run(win: CompositorWindow) -> None:
    x, y = 30, 30
    vx, vy = 4, 3

    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True

    win.set_event_handler(on_event)

    s = win.surface
    while not closed and not win._closed:
        # Clear and draw
        SDL_FillRect(s, None, _BG)
        SDL_FillRect(s, SDL_Rect(x, y, _BALL_SIZE, _BALL_SIZE), _BALL)
        win.dirty = True

        # Step
        x += vx; y += vy
        if x < 0 or x + _BALL_SIZE > _WIDTH:
            vx = -vx; x = max(0, min(_WIDTH - _BALL_SIZE, x))
        if y < 0 or y + _BALL_SIZE > _HEIGHT:
            vy = -vy; y = max(0, min(_HEIGHT - _BALL_SIZE, y))

        await asyncio.sleep(1.0 / 30)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Bouncing Ball", x=80, y=80,
                            w=_WIDTH, h=_HEIGHT)
    compositor.add_window(win)
    await _run(win)


from apps._icons import bouncing_ball_icon

registry.register(
    name="bouncing_ball",
    description="A bouncing rectangle (graphics demo)",
    entry=main,
    icon_factory=bouncing_ball_icon,
    category="demo",
)

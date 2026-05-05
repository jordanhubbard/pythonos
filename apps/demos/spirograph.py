"""apps.demos.spirograph — Logo-style turtle drawing a Spirograph.

Showcases :mod:`kernel.turtle`. Cycles through a few classic
parametric figures (rosette, flower, star) with a different pen
color each time. Once the figure is drawn the demo waits for ESC
or a click; left + right click cycles to the next pattern.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE
import kernel.turtle as t


_W = 480
_H = 320
_PATTERNS = ["rosette", "flower", "star", "polygon"]


def _draw_rosette():
    t.color(0xFF, 0xC0, 0x40)
    for _ in range(36):
        t.forward(80)
        t.right(170)


def _draw_flower():
    t.color(0xC0, 0xE0, 0xFF)
    for petal in range(12):
        for _ in range(36):
            t.forward(2)
            t.right(10)
        t.right(30)


def _draw_star():
    t.color(0xE0, 0x40, 0xC0)
    for _ in range(5):
        t.forward(100)
        t.right(144)


def _draw_polygon():
    t.color(0x60, 0xE0, 0x80)
    sides = 7
    for _ in range(sides):
        t.forward(80)
        t.right(360 / sides)


_DRAWS = {
    "rosette":  _draw_rosette,
    "flower":   _draw_flower,
    "star":     _draw_star,
    "polygon":  _draw_polygon,
}


def spirograph_icon():
    s = _new_icon(0x101830)
    _border(s, 0xFFC040)
    # Stylized rosette — three thin diamonds rotated.
    SDL_FillRect(s, SDL_Rect(22, 8, 4, 32), 0xFFC040)
    SDL_FillRect(s, SDL_Rect(8, 22, 32, 4), 0xFFC040)
    SDL_FillRect(s, SDL_Rect(12, 12, 24, 2), 0xFFC040)
    SDL_FillRect(s, SDL_Rect(12, 34, 24, 2), 0xFFC040)
    return s


async def main(*args, **kwargs) -> None:
    # Ensure the compositor is up before turtle tries to open a window.
    if not compositor._running:
        compositor.start()

    state = {"closed": False, "advance": False, "idx": 0}

    # Open a tiny status window so the user knows what's drawing.
    info = CompositorWindow("Spirograph", x=580, y=180, w=260, h=80)
    compositor.add_window(info)

    def on_event(ev):
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            state["closed"] = True
        elif ev.kind == _gui_input.MOUSE_DOWN:
            state["advance"] = True
    info.set_event_handler(on_event)

    try:
        while not state["closed"] and not info._closed:
            pat = _PATTERNS[state["idx"] % len(_PATTERNS)]
            t.clear()
            t.home()
            SDL_FillRect(info.surface, None, 0x101820)
            info.surface.draw_text(8, 8, f"Pattern: {pat}",
                                    fg=0xE0E0E0, bg=0x101820)
            info.surface.draw_text(8, 22, "(click → next, ESC → quit)",
                                    fg=0x808898, bg=0x101820)
            info.dirty = True

            _DRAWS[pat]()

            # Wait for click or close.
            state["advance"] = False
            while not state["advance"] and not state["closed"] \
                    and not info._closed:
                await asyncio.sleep(0.1)
            state["idx"] += 1
    finally:
        t.close()
        info.close()


registry.register(
    name="spirograph",
    description="Logo turtle drawing classic Spirograph patterns",
    entry=main,
    icon_factory=spirograph_icon,
    category="demo",
)

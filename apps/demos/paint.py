"""apps.demos.paint — Mouse-driven painting demo.

Showcases the bridge's input + drawing loop: as the mouse moves
with a button held, we drop colored squares along the cursor path.
Number keys 1-7 select a color from the palette; ``c`` clears the
canvas; ``ESC`` closes the window.

This is intentionally one of the simplest demos that exercises every
half of the SDL bridge — events flowing in, fill_rect ops flowing
out — so it doubles as a reference for new bridge consumers.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import paint_icon


_BG = 0xF8F8F8
_W = 480
_H = 320
_BRUSH = 4

# Keyed 1..7
_PALETTE = [
    0x101010,   # 1: black
    0xE03020,   # 2: red
    0xE08020,   # 3: orange
    0xE0C020,   # 4: yellow
    0x30A030,   # 5: green
    0x2060E0,   # 6: blue
    0x8030C0,   # 7: purple
]


async def _run(win: CompositorWindow) -> None:
    SDL_FillRect(win.surface, None, _BG)
    win.dirty = True

    state = {
        "color": _PALETTE[5],   # blue
        "drawing": False,
        "closed": False,
        "last_xy": None,
    }

    def _stroke_segment(x0: int, y0: int, x1: int, y1: int) -> None:
        # Stamp brush squares along the line — keeps strokes connected
        # when the mouse moves faster than the event rate.
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            t = i / steps
            x = int(x0 + (x1 - x0) * t)
            y = int(y0 + (y1 - y0) * t)
            SDL_FillRect(win.surface,
                         SDL_Rect(x - _BRUSH // 2, y - _BRUSH // 2,
                                  _BRUSH, _BRUSH),
                         state["color"])
        win.dirty = True

    def _stamp(x: int, y: int) -> None:
        SDL_FillRect(win.surface,
                     SDL_Rect(x - _BRUSH // 2, y - _BRUSH // 2,
                              _BRUSH, _BRUSH),
                     state["color"])
        win.dirty = True

    def _on_event(ev) -> None:
        if ev.kind == _gui_input.MOUSE_DOWN:
            state["drawing"] = True
            state["last_xy"] = (ev.x, ev.y)
            _stamp(ev.x, ev.y)
            return
        if ev.kind == _gui_input.MOUSE_UP:
            state["drawing"] = False
            state["last_xy"] = None
            return
        if ev.kind == _gui_input.MOUSE_MOVE and state["drawing"]:
            last = state["last_xy"]
            if last is not None:
                _stroke_segment(last[0], last[1], ev.x, ev.y)
            state["last_xy"] = (ev.x, ev.y)
            return
        if ev.kind == _gui_input.EVENT_KEY_DOWN:
            if ev.code == _gui_input.KEY_ESC:
                state["closed"] = True
                return
            if ev.text and len(ev.text) == 1:
                if "1" <= ev.text <= "7":
                    state["color"] = _PALETTE[ord(ev.text) - ord("1")]
                elif ev.text in ("c", "C"):
                    SDL_FillRect(win.surface, None, _BG)
                    win.dirty = True

    win.set_event_handler(_on_event)

    while not state["closed"] and not win._closed:
        await asyncio.sleep(1.0 / 60)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Paint", x=140, y=120, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="paint",
    description="Mouse-driven paint demo (1-7 colors, c clears)",
    entry=main,
    icon_factory=paint_icon,
    category="demo",
)

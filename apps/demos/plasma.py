"""apps.demos.plasma — Classic palette-cycled plasma effect.

A small (160x120) guest-backed buffer is recomputed each frame using
four superimposed sine waves; the buffer is blitted into the window
surface so the per-frame cost is one upload + one host-side blit.

Uses a precomputed 256-entry sine LUT and a 256-entry palette so the
inner loop is integer-only — no math.sin() in the hot path.
"""

import asyncio
import math

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import (
    SDL_Surface, SDL_Rect, SDL_BlitSurface, SDL_FillRect,
)
from apps import registry
from apps._icons import _new_icon, _border


_W = 160
_H = 120
_WIDTH  = _W
_HEIGHT = _H


def _build_sin_lut() -> list:
    # 256-entry sin table, range [0, 255]. Indexed mod 256.
    return [int(127 + 127 * math.sin(2 * math.pi * i / 256)) for i in range(256)]


def _build_palette() -> list:
    # Smooth fire-gradient palette, 256 entries → packed XRGB int.
    pal = []
    for i in range(256):
        # Three offset sine waves for the channels — gives a smooth cycle.
        r = int(127 + 127 * math.sin(2 * math.pi * i / 256))
        g = int(127 + 127 * math.sin(2 * math.pi * i / 256 + 2.0))
        b = int(127 + 127 * math.sin(2 * math.pi * i / 256 + 4.0))
        pal.append((r << 16) | (g << 8) | b)
    return pal


_SIN = _build_sin_lut()
_PAL = _build_palette()


async def _run(win: CompositorWindow) -> None:
    # Guest-backed scratch surface — direct bytearray access for the
    # per-pixel computation; blit dispatches as one host upload + blit.
    buf = SDL_Surface(_W, _H, host_backed=False)
    px = buf.pixels

    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True

    win.set_event_handler(on_event)

    # Pre-bake the per-row and per-column components that don't depend
    # on time. Each row/col contributes one phase shift; the time term
    # rotates them.
    row_phase = [(y * 6) & 0xFF for y in range(_H)]
    col_phase = [(x * 4) & 0xFF for x in range(_W)]
    diag_phase = [((x + y) * 5) & 0xFF for y in range(_H) for x in range(_W)]

    SIN = _SIN
    PAL = _PAL

    t = 0
    while not closed and not win._closed:
        # Each frame we shift four sine inputs by the current time t.
        # Combining four LUT lookups produces the classic plasma look
        # without ever calling math.sin in the hot path.
        t1 = t & 0xFF
        t2 = (t * 2) & 0xFF
        t3 = (t * 3) & 0xFF
        t4 = (t // 2) & 0xFF

        for y in range(_H):
            ry = (row_phase[y] + t1) & 0xFF
            sy = SIN[ry]
            ay_base = (row_phase[y] + t4) & 0xFF
            row_off = y * _W * 4
            diag_row = y * _W
            for x in range(_W):
                rx = (col_phase[x] + t2) & 0xFF
                rd = (diag_phase[diag_row + x] + t3) & 0xFF
                ra = (col_phase[x] + ay_base) & 0xFF
                v = (sy + SIN[rx] + SIN[rd] + SIN[ra]) >> 2   # back to 0..255
                color = PAL[v]
                o = row_off + x * 4
                px[o]     =  color        & 0xFF   # B
                px[o + 1] = (color >>  8) & 0xFF   # G
                px[o + 2] = (color >> 16) & 0xFF   # R
                px[o + 3] = 0xFF                    # X

        buf.dirty = True
        SDL_BlitSurface(buf, None, win.surface, SDL_Rect(0, 0, _W, _H))
        win.dirty = True

        t = (t + 3) & 0xFFFF
        await asyncio.sleep(1.0 / 20)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Plasma", x=160, y=140,
                            w=_WIDTH, h=_HEIGHT)
    compositor.add_window(win)
    await _run(win)


def plasma_icon():
    s = _new_icon(0x301050)
    _border(s, 0x603090)
    # A few representative palette colors as a swatch grid.
    swatch = (0xFF4040, 0xFFB040, 0xFFFF40, 0x40FF80,
              0x40C0FF, 0x4040FF, 0xC040FF, 0xFF40C0)
    for i, c in enumerate(swatch):
        x = 6 + (i % 4) * 9
        y = 8 + (i // 4) * 16
        SDL_FillRect(s, SDL_Rect(x, y, 8, 8), c)
    return s


registry.register(
    name="plasma",
    description="Palette-cycled plasma effect (graphics demo)",
    entry=main,
    icon_factory=plasma_icon,
    category="demo",
)

"""apps.demos.mandelbrot — Interactive Mandelbrot set explorer.

The kind of demo every 1982 hobbyist wanted to write but couldn't,
because their 1 MHz 6502 with software floats took an hour to render
a single 280x192 frame. Now Python at the metal does the inner loop
with complex numbers and an honest abs() check, the framebuffer is
real, and the mouse picks the next zoom target.

Controls:
  Click            zoom in 2x at the cursor
  Right-click      zoom out 2x
  r                reset view
  +  /  -          increase / decrease iteration depth
  c                next color palette
  ESC              close

The palette is the classic "fire" gradient — blue → magenta → yellow
→ white at the boundary. Pixel block size is configurable so coarse
draws are instant; full-detail renders hold the screen for a few
seconds while the boundary gets traced.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE


_W = 480
_H = 320
_BG = 0x000000
_PIXEL = 4               # cell size; lowering this gives more detail
_DEFAULT_ITER = 64
_MAX_ITER_CAP = 512


def _palette_fire(n: int, max_iter: int) -> int:
    if n >= max_iter:
        return 0x000000
    t = n / max_iter
    # blue → magenta → orange → white
    if t < 0.25:
        # 0x000040 → 0xC020E0
        u = t / 0.25
        r = int(0xC0 * u)
        g = int(0x20 * u)
        b = 0x40 + int((0xE0 - 0x40) * u)
    elif t < 0.6:
        u = (t - 0.25) / 0.35
        r = 0xC0 + int((0xFF - 0xC0) * u)
        g = 0x20 + int((0xC0 - 0x20) * u)
        b = int(0xE0 * (1 - u))
    else:
        u = (t - 0.6) / 0.4
        r = 0xFF
        g = 0xC0 + int((0xFF - 0xC0) * u)
        b = int(0xE0 * u)
    return (r << 16) | (g << 8) | b


def _palette_arctic(n: int, max_iter: int) -> int:
    if n >= max_iter:
        return 0x000000
    t = n / max_iter
    # deep blue → ice → white
    r = int(0xC0 * (t ** 1.4))
    g = int(0xE8 * (t ** 0.8))
    b = 0x30 + int((0xFF - 0x30) * (t ** 0.4))
    return (r << 16) | (g << 8) | b


def _palette_ember(n: int, max_iter: int) -> int:
    if n >= max_iter:
        return 0x000000
    t = n / max_iter
    # black → red → orange → yellow → white
    r = int(min(255, 0xFF * (t * 1.6)))
    g = int(0xE0 * (t ** 1.6))
    b = int(0xFF * (max(0.0, t - 0.7) / 0.3) ** 1.2) if t > 0.7 else 0
    return (r << 16) | (g << 8) | b


_PALETTES = (_palette_fire, _palette_arctic, _palette_ember)


def _iter(c: complex, max_iter: int) -> int:
    z = 0j
    for n in range(max_iter):
        z = z * z + c
        if z.real * z.real + z.imag * z.imag > 4.0:
            return n
    return max_iter


async def _render(win: CompositorWindow, view: dict, palette,
                  on_progress) -> None:
    """Re-render the current view. Yields control between row blocks
    so the compositor stays responsive and the user can interrupt by
    clicking again. on_progress(done, total) updates the status line."""
    cx, cy, span = view["cx"], view["cy"], view["span"]
    max_iter = view["max_iter"]
    cells_x = _W // _PIXEL
    cells_y = _H // _PIXEL
    aspect = _H / _W
    half_w = span / 2
    half_h = span * aspect / 2
    x0 = cx - half_w
    y0 = cy - half_h
    dx = span / cells_x
    dy = (span * aspect) / cells_y
    cancel = view["render_seq"]

    surface = win.surface
    SDL_FillRect(surface, None, _BG)
    win.dirty = True

    for ry in range(cells_y):
        if cancel != view["render_seq"]:
            return                       # superseded by another render
        cy_world = y0 + ry * dy
        for rx in range(cells_x):
            cx_world = x0 + rx * dx
            n = _iter(complex(cx_world, cy_world), max_iter)
            color = palette(n, max_iter)
            SDL_FillRect(surface,
                         SDL_Rect(rx * _PIXEL, ry * _PIXEL,
                                  _PIXEL, _PIXEL), color)
        win.dirty = True
        if (ry & 7) == 0:
            on_progress(ry, cells_y)
            await asyncio.sleep(0)         # cooperate
    on_progress(cells_y, cells_y)


def mandelbrot_icon():
    """Stylized M with bulb shape."""
    s = _new_icon(0x000020)
    _border(s, 0xC020E0)
    # Left vertical
    SDL_FillRect(s, SDL_Rect(10, 10, 4, 28), 0xFFE0A0)
    # Right vertical
    SDL_FillRect(s, SDL_Rect(34, 10, 4, 28), 0xFFE0A0)
    # Diagonals meeting in middle
    for i in range(12):
        SDL_FillRect(s, SDL_Rect(14 + i, 10 + i, 2, 2), 0xFF6020)
        SDL_FillRect(s, SDL_Rect(34 - i, 10 + i, 2, 2), 0xFF6020)
    return s


async def _run(win: CompositorWindow) -> None:
    view = {
        "cx": -0.5,
        "cy": 0.0,
        "span": 3.2,
        "max_iter": _DEFAULT_ITER,
        "render_seq": 0,
        "palette_idx": 0,
        "status": "rendering...",
        "closed": False,
        "dirty_pending": True,
    }

    def status(done, total):
        view["status"] = (f"{view['cx']:+.5f},{view['cy']:+.5f}  "
                          f"span={view['span']:.5f}  "
                          f"iter={view['max_iter']}  "
                          f"{int(done * 100 / max(total, 1))}%")

    def trigger_render():
        view["render_seq"] += 1
        view["dirty_pending"] = True

    def on_event(ev):
        if ev.kind == _gui_input.KEY_DOWN:
            if ev.code == _gui_input.KEY_ESC:
                view["closed"] = True
            elif ev.text == "r":
                view["cx"] = -0.5
                view["cy"] = 0.0
                view["span"] = 3.2
                view["max_iter"] = _DEFAULT_ITER
                trigger_render()
            elif ev.text == "+":
                view["max_iter"] = min(_MAX_ITER_CAP,
                                        view["max_iter"] * 2)
                trigger_render()
            elif ev.text == "-":
                view["max_iter"] = max(8, view["max_iter"] // 2)
                trigger_render()
            elif ev.text == "c":
                view["palette_idx"] = (view["palette_idx"] + 1) \
                                       % len(_PALETTES)
                trigger_render()
        elif ev.kind == _gui_input.MOUSE_DOWN:
            # Map screen pixel to world coordinate, then zoom.
            aspect = _H / _W
            world_x = view["cx"] + ((ev.x / _W) - 0.5) * view["span"]
            world_y = view["cy"] + ((ev.y / _H) - 0.5) * view["span"] * aspect
            view["cx"] = world_x
            view["cy"] = world_y
            if ev.code == 3:           # right button → zoom out
                view["span"] *= 2
            else:
                view["span"] /= 2
            trigger_render()

    win.set_event_handler(on_event)

    while not view["closed"] and not win._closed:
        if view["dirty_pending"]:
            view["dirty_pending"] = False
            palette = _PALETTES[view["palette_idx"]]
            await _render(win, view, palette, status)
        # Status line at the bottom — redrawn each tick so the % updates.
        SDL_FillRect(win.surface, SDL_Rect(0, _H - 16, _W, 16), 0x101820)
        win.surface.draw_text(6, _H - 14, view["status"][:80],
                               fg=0xC0E0FF, bg=0x101820)
        win.dirty = True
        await asyncio.sleep(1.0 / 30)
    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Mandelbrot Explorer",
                            x=140, y=120, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="mandelbrot",
    description="Mandelbrot explorer — click to zoom, r resets, c cycles palette",
    entry=main,
    icon_factory=mandelbrot_icon,
    category="demo",
)

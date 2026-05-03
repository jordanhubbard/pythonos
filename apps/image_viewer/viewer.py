"""apps.image_viewer.viewer — Centred image display in a CompositorWindow.

Uses :func:`kernel.gui.image.load` for the decode (currently BMP + PPM;
PNG / JPEG arrive with the decoder follow-ups). The window is sized to
the loaded image (capped to a sensible maximum) and the pixels are
blitted into its surface.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui import image as _image
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_BlitSurface, SDL_Rect
from apps import registry


_MAX_W = 1000
_MAX_H = 700
_BG = 0x202028
_FG = 0xFFFFFF


def _draw_message(win: CompositorWindow, text: str) -> None:
    SDL_FillRect(win.surface, None, _BG)
    win.surface.draw_text(8, 8, text, fg=_FG, bg=_BG)
    win.dirty = True


async def main(argv=None, *args, **kwargs) -> None:
    argv = list(argv) if argv else []
    if not argv:
        win = CompositorWindow("image_viewer", x=120, y=120, w=400, h=80)
        compositor.add_window(win)
        _draw_message(win, "usage: image_viewer <path>")
        await _wait_close(win)
        return

    path = argv[0]
    try:
        surf = _image.load(path)
    except Exception as e:
        win = CompositorWindow("image_viewer", x=120, y=120, w=600, h=80)
        compositor.add_window(win)
        _draw_message(win, "load failed: " + str(e))
        await _wait_close(win)
        return

    w = min(surf.w, _MAX_W)
    h = min(surf.h, _MAX_H)
    win = CompositorWindow(path, x=80, y=80, w=w, h=h)
    compositor.add_window(win)
    SDL_FillRect(win.surface, None, _BG)
    SDL_BlitSurface(surf, None, win.surface, SDL_Rect(0, 0, w, h))
    win.dirty = True

    await _wait_close(win)


async def _wait_close(win: CompositorWindow) -> None:
    closed = False
    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True
    win.set_event_handler(on_event)
    while not closed and not win._closed:
        await asyncio.sleep(0.05)
    win.close()


from apps._icons import image_viewer_icon

registry.register(
    name="image_viewer",
    description="Display BMP / PPM images (PNG/JPEG via follow-up decoders)",
    entry=main,
    icon_factory=image_viewer_icon,
)

"""apps.about.about — "About PythonOS" window.

Static window that introduces the system, lists what makes it
unusual (CPython 3.14 is the kernel; no POSIX), and shows the
current build's runtime stats. ESC or click closes it.
"""

import asyncio
import sys

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE


_W = 480
_H = 280
_BG = 0x101820
_PANEL = 0x182030
_FG = 0xE0E0E0
_DIM = 0xA0A8B0
_ACCENT = 0x60D0FF


def about_icon():
    s = _new_icon(0x182030)
    _border(s, 0x60D0FF)
    # Stylized i in a circle
    SDL_FillRect(s, SDL_Rect(20, 14, 8, 4), 0x60D0FF)        # dot
    SDL_FillRect(s, SDL_Rect(22, 22, 4, 14), 0x60D0FF)       # stem
    SDL_FillRect(s, SDL_Rect(18, 22, 12, 2), 0x60D0FF)       # cap top
    SDL_FillRect(s, SDL_Rect(16, 34, 16, 2), 0x60D0FF)       # cap bottom
    return s


def _arch() -> str:
    try:
        import _hal
        return getattr(_hal, "ARCH", "?")
    except Exception:
        return "?"


def _smp_str() -> str:
    try:
        import _hal
        return f"{getattr(_hal, 'SMP_ONLINE', '?')}"
    except Exception:
        return "?"


def _free_mib() -> int:
    try:
        import kernel.memory.vmm as _vmm
        if _vmm.vmm is not None and _vmm.vmm.pmm is not None:
            return _vmm.vmm.pmm.free_pages * 4 // 1024
    except Exception:
        pass
    return 0


async def _run(win: CompositorWindow) -> None:
    closed = False

    def on_event(ev) -> None:
        nonlocal closed
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True
        elif ev.kind == _gui_input.MOUSE_DOWN:
            closed = True

    win.set_event_handler(on_event)
    s = win.surface

    SDL_FillRect(s, None, _BG)
    # Header band
    SDL_FillRect(s, SDL_Rect(0, 0, _W, 56), _PANEL)
    s.draw_text(20, 16, "PythonOS", fg=_ACCENT, bg=_PANEL)
    s.draw_text(20, 32, "Python is the kernel.", fg=_FG, bg=_PANEL)

    # Body lines
    y = 76
    lines = [
        "Bare-metal CPython 3.14 — boots into a Python REPL with no",
        "POSIX between you and the hardware.",
        "",
        f"  Python    {sys.version.split()[0]}",
        f"  Arch      {_arch()}",
        f"  SMP CPUs  {_smp_str()}",
        f"  Free RAM  {_free_mib()} MiB",
        "",
        "  Apps      type 'pythonos_gui' at the REPL or",
        "            click an icon in the dock.",
        "",
        "  Goals     no compromise of the boot-to-prompt path;",
        "            full Python interpreter at the prompt;",
        "            SDL2-compatible client API via the bridge.",
    ]
    for line in lines:
        if line:
            s.draw_text(20, y, line, fg=_FG, bg=_BG)
        y += 14

    s.draw_text(_W // 2 - 36, _H - 24,
                "(click or ESC)", fg=_DIM, bg=_BG)
    win.dirty = True

    while not closed and not win._closed:
        await asyncio.sleep(1.0 / 30)
    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("About PythonOS",
                            x=300, y=180, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="about",
    description="About PythonOS — version + system info",
    entry=main,
    icon_factory=about_icon,
)

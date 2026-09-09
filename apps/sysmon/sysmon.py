"""apps.sysmon.sysmon — Live kernel state in a window.

Refreshes every 500 ms. Top panel shows uptime + free RAM + a small
animated history graph for the latter; bottom panel lists current
scheduler tasks. Useful both as a demo of the SDL bridge text path
and as a quick visual confirmation that the kernel is healthy.

ESC closes the window.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from kernel.scheduler import scheduler
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE


_W = 480
_H = 320
_BG = 0x101820
_FG = 0xE0E0E0
_PANEL = 0x182030
_ACCENT = 0x60D0FF
_GRAPH_H = 48
_REFRESH_HZ = 2


def sysmon_icon():
    """Bar-chart glyph in PythonOS palette."""
    s = _new_icon(0x101820)
    _border(s, 0x60A0E0)
    bars = [(8, 32, 6, 8), (16, 26, 6, 14), (24, 20, 6, 20),
            (32, 16, 6, 24), (40, 12, 4, 28)]
    for x, y, w, h in bars:
        SDL_FillRect(s, SDL_Rect(x, y, w, h), 0x60D0FF)
    SDL_FillRect(s, SDL_Rect(4, ICON_SIZE - 6, ICON_SIZE - 8, 1), 0xFFFFFF)
    return s


def _free_mib() -> int:
    try:
        from kernel.memory.pmm import PhysicalMemoryManager  # noqa
        # The boot PMM is bound to a non-global; easiest path: reach into the
        # kernel module that holds the live VMM (which itself holds a pmm
        # reference). VMM is created in kernel/__init__.py and stored on the
        # module. Fall back to 0 if anything's not wired.
        import kernel.memory.vmm as _vmm
        if _vmm.vmm is None or _vmm.vmm.pmm is None:
            return 0
        return _vmm.vmm.pmm.free_pages * 4 // 1024
    except Exception:
        return 0


async def _run(win: CompositorWindow) -> None:
    closed = False

    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True

    win.set_event_handler(on_event)

    history: list[int] = []
    surface = win.surface

    while not closed and not win._closed:
        SDL_FillRect(surface, None, _BG)

        # ── Header panel ────────────────────────────────────────────────
        SDL_FillRect(surface, SDL_Rect(0, 0, _W, _GRAPH_H + 32), _PANEL)

        uptime_s = scheduler.uptime_ms // 1000
        h = uptime_s // 3600
        m = (uptime_s % 3600) // 60
        s = uptime_s % 60
        free = _free_mib()
        history.append(free)
        if len(history) > _W // 2:
            history = history[-(_W // 2):]

        surface.draw_text(8, 8,
                          f"uptime  {h:02d}:{m:02d}:{s:02d}",
                          fg=_FG, bg=_PANEL)
        surface.draw_text(8, 18,
                          f"free RAM  {free} MiB",
                          fg=_ACCENT, bg=_PANEL)

        # Mini graph of free RAM over time — just colored vertical bars.
        if history:
            peak = max(max(history), 1)
            base_y = _GRAPH_H + 28
            for i, v in enumerate(history):
                bar_h = max(1, (v * (_GRAPH_H - 4)) // peak)
                bar_x = 8 + i * 2
                SDL_FillRect(surface,
                             SDL_Rect(bar_x, base_y - bar_h, 2, bar_h),
                             _ACCENT)

        # ── Process panel ───────────────────────────────────────────────
        pid_y = _GRAPH_H + 40
        surface.draw_text(8, pid_y,
                          " PID  STATE     NAME",
                          fg=_FG, bg=_BG)
        pid_y += 14
        for proc in list(scheduler.ps())[:14]:
            state = proc.state.name[:7] if hasattr(proc.state, "name") \
                else str(proc.state)[:7]
            line = f"{proc.pid:>4}  {state:<8}  {proc.name[:48]}"
            surface.draw_text(8, pid_y, line, fg=_FG, bg=_BG)
            pid_y += 12

        win.dirty = True
        await asyncio.sleep(1.0 / _REFRESH_HZ)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("System Monitor",
                            x=200, y=140, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="sysmon",
    description="Live kernel state — uptime, free RAM, processes",
    entry=main,
    icon_factory=sysmon_icon,
)

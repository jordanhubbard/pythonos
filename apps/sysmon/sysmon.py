"""apps.sysmon.sysmon — ``top``-style live kernel state and performance.

Refreshes the display twice per second but samples host-side bridge metrics
only every two seconds, keeping observation overhead bounded.

ESC closes the window.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from kernel.scheduler import scheduler
from kernel.bridge import bridge
from kernel.sound.mixer import mixer
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE


_W = 760
_H = 500
_BG = 0x101820
_FG = 0xE0E0E0
_DIM = 0x8090A0
_PANEL = 0x182030
_ACCENT = 0x60D0FF
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
    paused = False
    reset_requested = False

    def on_event(ev):
        nonlocal closed, paused, reset_requested
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            closed = True
        elif ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code in (ord("p"), ord("P")):
            paused = not paused
        elif ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code in (ord("r"), ord("R")):
            reset_requested = True

    win.set_event_handler(on_event)

    surface = win.surface
    snapshot = {"guest": {}, "host": {}}
    sample_number = 0

    while not closed and not win._closed:
        reset_now = reset_requested
        if not paused:
            if sample_number % 4 == 0 or reset_now:
                snapshot = bridge.performance_snapshot(reset=reset_now)
                reset_requested = False
            else:
                snapshot["guest"] = bridge.metrics()
            sample_number += 1
        SDL_FillRect(surface, None, _BG)

        SDL_FillRect(surface, SDL_Rect(0, 0, _W, 64), _PANEL)

        uptime_s = scheduler.uptime_ms // 1000
        h = uptime_s // 3600
        m = (uptime_s % 3600) // 60
        s = uptime_s % 60
        free = _free_mib()
        tasks = list(scheduler.ps())
        surface.draw_text(8, 8, "PYTHONOS TOP" + ("  [PAUSED]" if paused else ""),
                          fg=_FG, bg=_PANEL)
        surface.draw_text(8, 25,
                          f"uptime {h:02d}:{m:02d}:{s:02d}   free {free} MiB"
                          f"   tasks {len(tasks)}   refresh {_REFRESH_HZ} Hz",
                          fg=_ACCENT, bg=_PANEL)
        surface.draw_text(8, 42,
                          "P pause   R reset counters   Esc close",
                          fg=_FG, bg=_PANEL)

        y = 76
        surface.draw_text(8, y, "BRIDGE RPC       CALLS   MEAN us   MAX us    TX/RX bytes",
                          fg=_FG, bg=_BG)
        y += 16
        guest = snapshot.get("guest", {})
        rows = sorted(guest.items(),
                      key=lambda item: item[1].get("total_ticks", 0), reverse=True)
        for name, values in rows[:8]:
            line = (f"{name[:15]:<15} {values.get('count', 0):>6}"
                    f" {values.get('mean_us', 0):>9} {values.get('max_us', 0):>8}"
                    f" {values.get('tx_bytes', 0):>7}/{values.get('rx_bytes', 0):<7}")
            surface.draw_text(8, y, line, fg=_ACCENT, bg=_BG)
            y += 14

        host = snapshot.get("host", {})
        host_ops = host.get("ops", {}) if isinstance(host, dict) else {}
        slow = sorted(host_ops.items(),
                      key=lambda item: item[1].get("total_ticks", 0), reverse=True)
        if slow:
            summary = "host service: " + "  ".join(
                name + " " + str(int(values.get("mean_us", 0))) + "us"
                for name, values in slow[:3])
            surface.draw_text(8, y + 2, summary[:90], fg=_DIM, bg=_BG)

        audio = mixer.performance_snapshot(reset=reset_now)
        stream = audio.get("stream", {})
        device = audio.get("device", {})
        audio_y = 224
        surface.draw_text(
            8, audio_y,
            ("AUDIO " + str(audio.get("backend") or "none")
             + " accepted/expected " + str(stream.get("accepted_periods", 0))
             + "/" + str(stream.get("expected_periods", 0))
             + " shortfall " + str(stream.get("delivery_shortfall", 0))
             + " retry " + str(stream.get("backpressure", 0))),
            fg=_ACCENT, bg=_BG)
        surface.draw_text(
            8, audio_y + 14,
            ("      DMA inflight/free " + str(device.get("inflight", 0))
             + "/" + str(device.get("free", 0))
             + " high " + str(device.get("high_water", 0))
             + " device-retry " + str(device.get("backpressure", 0))),
            fg=_DIM, bg=_BG)

        pid_y = 258
        surface.draw_text(8, pid_y,
                          " PID  STATE       TICKS  NAME",
                          fg=_FG, bg=_BG)
        pid_y += 14
        for proc in tasks[:18]:
            state = proc.state.name[:7] if hasattr(proc.state, "name") \
                else str(proc.state)[:7]
            line = f"{proc.pid:>4}  {state:<8} {proc.ticks:>8}  {proc.name[:56]}"
            surface.draw_text(8, pid_y, line, fg=_FG, bg=_BG)
            pid_y += 12

        win.dirty = True
        await asyncio.sleep(1.0 / _REFRESH_HZ)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Top", x=120, y=90, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="top",
    description="Top — live tasks and performance statistics",
    entry=main,
    icon_factory=sysmon_icon,
)

# Shell/API compatibility without a duplicate dock icon or Apps-menu row.
registry.register(name="sysmon", description="", entry=main,
                  icon_factory=sysmon_icon, category="compat")

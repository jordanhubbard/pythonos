"""apps.demos.keyboard — Live keyboard event visualizer.

Each KEY_DOWN, KEY_UP, MOUSE_DOWN, MOUSE_UP, MOUSE_MOVE event
appears as a row in a scrolling log. Useful for verifying input
plumbing on a new arch and as a reference for "what does the
event queue look like."

Click anywhere or press ESC to close.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry
from apps._icons import keyboard_demo_icon


_W = 520
_H = 320
_BG = 0x101820
_PANEL = 0x182030
_FG = 0xE0E0E0
_DIM = 0x808898
_KEY_DOWN_COLOR = 0x60D0FF
_KEY_UP_COLOR = 0xA08070
_MOUSE_COLOR = 0xC0E060
_HEADER_H = 28
_LINE_H = 14
_LOG_PAD = 6


def _kind_label(kind: int) -> str:
    if kind == _gui_input.KEY_DOWN:    return "KEY_DOWN"
    if kind == _gui_input.KEY_UP:      return "KEY_UP"
    if kind == _gui_input.MOUSE_MOVE:  return "MOUSE_MOVE"
    if kind == _gui_input.MOUSE_DOWN:  return "MOUSE_DOWN"
    if kind == _gui_input.MOUSE_UP:    return "MOUSE_UP"
    if kind == _gui_input.MOUSE_WHEEL: return "MOUSE_WHEEL"
    if kind == _gui_input.QUIT:        return "QUIT"
    return f"kind={kind}"


def _kind_color(kind: int) -> int:
    if kind == _gui_input.KEY_DOWN: return _KEY_DOWN_COLOR
    if kind == _gui_input.KEY_UP:   return _KEY_UP_COLOR
    return _MOUSE_COLOR


async def _run(win: CompositorWindow) -> None:
    log: list[tuple[int, str]] = []
    max_lines = (_H - _HEADER_H - _LOG_PAD * 2) // _LINE_H
    state = {"closed": False}

    def fmt(ev) -> str:
        kind = _kind_label(ev.kind)
        bits = [f"{kind:<11}"]
        if ev.code:
            ch = chr(ev.code) if 32 <= ev.code < 127 else "?"
            bits.append(f"code={ev.code:#x} ('{ch}')")
        if ev.text:
            t = ev.text if ev.text.isprintable() else repr(ev.text)
            bits.append(f"text={t!r}")
        if ev.x or ev.y:
            bits.append(f"xy=({ev.x},{ev.y})")
        if ev.dx or ev.dy:
            bits.append(f"d=({ev.dx},{ev.dy})")
        if ev.mods:
            bits.append(f"mods={ev.mods:#x}")
        return "  ".join(bits)

    def on_event(ev) -> None:
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            state["closed"] = True
            return
        log.append((ev.kind, fmt(ev)))
        while len(log) > max_lines:
            log.pop(0)

    win.set_event_handler(on_event)
    s = win.surface

    while not state["closed"] and not win._closed:
        SDL_FillRect(s, None, _BG)
        SDL_FillRect(s, SDL_Rect(0, 0, _W, _HEADER_H), _PANEL)
        s.draw_text(_LOG_PAD, 6, "Keyboard / Mouse event monitor",
                    fg=_FG, bg=_PANEL)
        s.draw_text(_LOG_PAD, 16,
                    "(any key + mouse button + motion shown; ESC to close)",
                    fg=_DIM, bg=_PANEL)
        y = _HEADER_H + _LOG_PAD
        for kind, line in log:
            s.draw_text(_LOG_PAD, y, line, fg=_kind_color(kind), bg=_BG)
            y += _LINE_H
        win.dirty = True
        await asyncio.sleep(1.0 / 30)
    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Keyboard Monitor",
                            x=200, y=140, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


registry.register(
    name="keyboard",
    description="Live keyboard / mouse event visualizer",
    entry=main,
    icon_factory=keyboard_demo_icon,
    category="demo",
)

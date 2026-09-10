"""A small control panel for desktop-wide keyboard shortcuts."""

import asyncio

from apps import registry
from apps._icons import keyboard_demo_icon
from kernel.gui import input as gui_input
from kernel.gui.compositor import CompositorWindow, compositor
from kernel.gui.keybindings import all_bindings, describe, reset, save, set_binding
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect


_W, _H = 560, 260
_BG, _FG, _DIM, _SELECT = 0x101820, 0xE0E0E0, 0x8090A0, 0x355088


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Keybindings", x=180, y=130, w=_W, h=_H)
    compositor.add_window(win)
    selected = 0
    capture = False
    closed = False
    needs_redraw = True
    message = "Enter changes a binding; R restores defaults"

    def on_event(ev):
        nonlocal selected, capture, closed, message, needs_redraw
        rows = all_bindings()
        if ev.kind == gui_input.MOUSE_DOWN:
            row = (ev.y - 54) // 28
            if 0 <= row < len(rows):
                selected = row
                needs_redraw = True
        if ev.kind != gui_input.EVENT_KEY_DOWN:
            return
        if capture:
            if ev.code == gui_input.KEY_ESC:
                capture = False
                message = "Change cancelled"
                needs_redraw = True
            elif ev.code not in (gui_input.KEY_LSHIFT, gui_input.KEY_RSHIFT,
                                  gui_input.KEY_LCTRL, gui_input.KEY_RCTRL,
                                  gui_input.KEY_LALT, gui_input.KEY_RALT):
                set_binding(rows[selected].action, ev.code, ev.mods)
                asyncio.get_event_loop().create_task(save())
                capture = False
                message = rows[selected].label + " = " + describe(rows[selected])
                needs_redraw = True
            return
        if ev.code == gui_input.KEY_ESC:
            closed = True
        elif ev.code == gui_input.KEY_UP:
            selected = max(0, selected - 1)
            needs_redraw = True
        elif ev.code == gui_input.KEY_DOWN:
            selected = min(len(rows) - 1, selected + 1)
            needs_redraw = True
        elif ev.code == gui_input.KEY_ENTER:
            capture = True
            message = "Press the new key combination (Esc cancels)"
            needs_redraw = True
        elif ev.code in (ord("r"), ord("R")):
            reset()
            asyncio.get_event_loop().create_task(save())
            message = "Desktop bindings restored"
            needs_redraw = True

    win.set_event_handler(on_event)
    while not closed and not win._closed:
        if not needs_redraw:
            await asyncio.sleep(0.05)
            continue
        SDL_FillRect(win.surface, None, _BG)
        win.surface.draw_text(12, 12, "DESKTOP KEYBINDINGS", fg=_FG, bg=_BG)
        win.surface.draw_text(12, 30, message, fg=_DIM, bg=_BG)
        for index, row in enumerate(all_bindings()):
            y = 54 + index * 28
            bg = _SELECT if index == selected else _BG
            SDL_FillRect(win.surface, SDL_Rect(8, y - 3, _W - 16, 22), bg)
            win.surface.draw_text(16, y, row.label[:34], fg=_FG, bg=bg)
            win.surface.draw_text(360, y, describe(row), fg=_FG, bg=bg)
        win.surface.draw_text(12, _H - 30,
                              "Esc always exits full-screen views (safety binding)",
                              fg=_DIM, bg=_BG)
        win.dirty = True
        needs_redraw = False
        await asyncio.sleep(0.05)
    win.close()


registry.register(name="keybindings",
                  description="View and change desktop keybindings",
                  entry=main, icon_factory=keyboard_demo_icon)

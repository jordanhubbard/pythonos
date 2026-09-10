"""apps.clock.clock — Big-digit session clock and kernel uptime display.

PythonOS has no persistent RTC yet. Press S (or use the Clock menu) to set a
24-hour session time; it advances from the monotonic kernel clock until reboot.
Press U to return to uptime. Every digit is rendered as a 5x7 glyph scaled up
3x using fill_rect — exercises the bridge's drawing path with no
text dependency, useful as a reference for how to do bespoke
glyph rendering on top of the SDL bridge.

ESC closes; click cycles between MM:SS and HH:MM:SS displays.
"""

import asyncio

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from kernel import timekeeper
from apps import registry
from apps._icons import _new_icon, _border, ICON_SIZE


_W = 360
_H = 160
_BG = 0x101820
_FG = 0x60D0FF
_DIM = 0x303848

# 5-wide x 7-tall pixel font for digits and ':' — minimal hand-drawn.
# Each glyph is 7 rows of 5 bits.
_GLYPHS = {
    "0": [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
    "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
    "2": [0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111],
    "3": [0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110],
    "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
    "5": [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
    "6": [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
    "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
    "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
    "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
    ":": [0b00000, 0b00100, 0b00100, 0b00000, 0b00100, 0b00100, 0b00000],
    " ": [0b00000] * 7,
}

_GLYPH_W = 5
_GLYPH_H = 7
_PIXEL = 5     # scale factor

CHAR_W = _GLYPH_W * _PIXEL
CHAR_H = _GLYPH_H * _PIXEL
SPACING = 4


def _draw_text(surface, x: int, y: int, text: str, fg: int) -> None:
    cx = x
    for ch in text:
        glyph = _GLYPHS.get(ch)
        if glyph is None:
            cx += CHAR_W + SPACING
            continue
        for row, bits in enumerate(glyph):
            for col in range(_GLYPH_W):
                if bits & (1 << (_GLYPH_W - 1 - col)):
                    SDL_FillRect(surface,
                                 SDL_Rect(cx + col * _PIXEL,
                                          y + row * _PIXEL,
                                          _PIXEL, _PIXEL), fg)
        cx += CHAR_W + SPACING


def clock_icon():
    s = _new_icon(0x101820)
    _border(s, 0x60D0FF)
    # Clock face: square outline + 3 hands
    SDL_FillRect(s, SDL_Rect(8, 8, 32, 32), 0x182030)
    # Hour
    SDL_FillRect(s, SDL_Rect(23, 16, 2, 8), 0xE0E0E0)
    # Minute
    SDL_FillRect(s, SDL_Rect(24, 24, 14, 2), 0x60D0FF)
    # Center dot
    SDL_FillRect(s, SDL_Rect(22, 22, 4, 4), 0xFFFFFF)
    return s


def _parse_time(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
        raise ValueError("use HH:MM or HH:MM:SS")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("time must be 00:00:00..23:59:59")
    return hour, minute, second


async def _run(win: CompositorWindow, state: dict) -> None:

    def on_event(ev) -> None:
        if ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            if state["editing"]:
                state["editing"] = False
                state["input"] = ""
                state["message"] = "Set cancelled"
            else:
                state["closed"] = True
        elif ev.kind == _gui_input.EVENT_KEY_DOWN and state["editing"]:
            if ev.code == _gui_input.KEY_ENTER:
                try:
                    timekeeper.set_hms(*_parse_time(state["input"]))
                    state["message"] = "Session time set; U restores uptime"
                    state["editing"] = False
                    state["input"] = ""
                except ValueError as exc:
                    state["message"] = str(exc)
            elif ev.code == _gui_input.KEY_BACKSPACE:
                state["input"] = state["input"][:-1]
            elif ev.text:
                for char in ev.text:
                    if (char.isdigit() or char == ":") and len(state["input"]) < 8:
                        state["input"] += char
        elif ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code in (ord("s"), ord("S")):
            state["editing"] = True
            state["input"] = ""
            state["message"] = ""
        elif ev.kind == _gui_input.EVENT_KEY_DOWN and ev.code in (ord("u"), ord("U")):
            timekeeper.clear()
            state["message"] = "Showing uptime"
        elif ev.kind == _gui_input.MOUSE_DOWN:
            state["show_h"] = not state["show_h"]

    win.set_event_handler(on_event)
    s = win.surface

    while not state["closed"] and not win._closed:
        SDL_FillRect(s, None, _BG)

        secs = timekeeper.seconds()
        hh = secs // 3600
        mm = (secs % 3600) // 60
        ss = secs % 60
        if state["show_h"]:
            text = f"{hh:02d}:{mm:02d}:{ss:02d}"
        else:
            text = f"{mm:02d}:{ss:02d}"
        text_w = len(text) * (CHAR_W + SPACING) - SPACING
        x = (_W - text_w) // 2
        y = (_H - CHAR_H) // 2 - 8
        # Faint shadow
        _draw_text(s, x + 2, y + 2, text, _DIM)
        _draw_text(s, x, y, text, _FG)
        # Caption
        if state["editing"]:
            caption = "Set HH:MM[:SS]: " + state["input"] + "_"
        elif state["message"]:
            caption = state["message"]
        elif timekeeper.is_set():
            caption = "session time  S:set U:uptime"
        else:
            caption = "uptime  S:set time"
        s.draw_text(8, _H - 24, caption[:43], fg=0x808898, bg=_BG)

        win.dirty = True
        await asyncio.sleep(0.5)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Clock", x=240, y=200, w=_W, h=_H)
    state = {"closed": False, "show_h": True, "editing": False,
             "input": "", "message": ""}

    def set_time() -> None:
        state["editing"] = True
        state["input"] = ""
        state["message"] = ""
        win.dirty = True

    def use_uptime() -> None:
        timekeeper.clear()
        state["editing"] = False
        state["message"] = "Showing uptime"
        win.dirty = True

    from kernel.gui.menubar import Menu, MenuItem
    win.menus = [Menu("Clock", [
        MenuItem("Set Time… (S)", action=set_time),
        MenuItem("Use Uptime (U)", action=use_uptime),
        MenuItem.sep(),
        MenuItem("Close (Esc)", action=win.close),
    ])]
    compositor.add_window(win)
    await _run(win, state)


registry.register(
    name="clock",
    description="Settable session clock and live kernel uptime",
    entry=main,
    icon_factory=clock_icon,
)

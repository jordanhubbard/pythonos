"""kernel.turtle — Logo-style turtle graphics for the REPL.

Every Apple ][ kid traced a square with FORWARD 100 / RIGHT 90 four
times. This is the same idea using real Python on the framebuffer.

First call to any drawing primitive opens a 480x320 turtle window.
The turtle starts at the centre, facing right (0°), pen down, in
yellow on a dark blue background. Subsequent commands move + draw
without reopening anything; ``home()`` returns to centre,
``clear()`` wipes the canvas, ``close()`` dismisses the window.

Synopsis::

    from kernel.turtle import *
    for _ in range(36):
        forward(80)
        right(170)            # the classic Spirograph rosette

Public API mirrors Python's stdlib ``turtle`` module loosely:

  - forward / fd, back / bk
  - right  / rt, left / lt
  - goto(x, y)   — absolute coords (canvas centre is origin, +y up)
  - setheading(deg) — absolute heading; 0 is east
  - pen_up / pu, pen_down / pd
  - color(r, g, b) — pen color (0-255 each)
  - bgcolor(r, g, b) — background; clears to that
  - home, clear, hide, show
  - position(), heading(), is_down() — getters
  - close() — dismiss the window

The turtle leaves a 1-pixel trail when the pen is down. Because the
bridge has a ``surface.line`` op now, each forward() is exactly one
host-side draw — even very long strokes paint instantly.
"""

import math


_state = {"win": None, "x": 0.0, "y": 0.0, "heading": 0.0,
          "pen": True, "pen_color": 0xFFD040, "bg": 0x101830,
          "shown": True, "task": None}

_W = 480
_H = 320


def _bridge():
    from kernel.bridge import bridge as _br
    return _br


def _ensure_window():
    if _state["win"] is not None and not _state["win"]._closed:
        return _state["win"]
    from kernel.gui.compositor import compositor, CompositorWindow
    from kernel.gui.sdl2.surface import SDL_FillRect
    from kernel.gui import input as _gui_input

    if not compositor._running:
        raise RuntimeError(
            "turtle: compositor isn't running yet. "
            "Run `pythonos_gui` first (or any app that starts it) "
            "and then try again — the turtle needs a window to draw in."
        )

    win = CompositorWindow("Turtle", x=180, y=140, w=_W, h=_H)
    compositor.add_window(win)
    SDL_FillRect(win.surface, None, _state["bg"])
    win.dirty = True

    def on_event(ev):
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_ESC:
            close()
    win.set_event_handler(on_event)

    _state["win"] = win
    _state["x"] = 0.0
    _state["y"] = 0.0
    _state["heading"] = 0.0
    return win


def _to_screen(x: float, y: float) -> tuple[int, int]:
    """Canvas (centre origin, +y up) → screen (top-left, +y down)."""
    return (int(_W / 2 + x), int(_H / 2 - y))


def _draw_turtle():
    """Re-render the triangle marker at the current pose. Called after
    each move so the user sees where the turtle is."""
    if not _state["shown"]:
        return
    win = _state["win"]
    if win is None or win._closed:
        return
    sx, sy = _to_screen(_state["x"], _state["y"])
    th = math.radians(_state["heading"])
    size = 6
    # Triangle: front + two back corners
    fx, fy = sx + int(size * math.cos(th)), sy - int(size * math.sin(th))
    lx, ly = (sx + int(size * 0.7 * math.cos(th + math.radians(140))),
              sy - int(size * 0.7 * math.sin(th + math.radians(140))))
    rx, ry = (sx + int(size * 0.7 * math.cos(th - math.radians(140))),
              sy - int(size * 0.7 * math.sin(th - math.radians(140))))
    color = 0xC0E0FF
    _line_op(fx, fy, lx, ly, color)
    _line_op(lx, ly, rx, ry, color)
    _line_op(rx, ry, fx, fy, color)


def _line_op(x0, y0, x1, y1, color: int):
    win = _state["win"]
    if win is None or win._closed:
        return
    surface = win.surface
    if surface.host_backed:
        word = (color & 0xFFFFFF) | 0xFF000000
        _bridge().cast("surface.line", {
            "handle": surface.handle,
            "x0": int(x0), "y0": int(y0),
            "x1": int(x1), "y1": int(y1),
            "rgb": word,
        })
    else:
        # Pure-Python Bresenham fallback.
        from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
        dx = abs(x1 - x0); sx_ = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0); sy_ = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            SDL_FillRect(surface, SDL_Rect(int(x0), int(y0), 1, 1), color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy; x0 += sx_
            if e2 <= dx:
                err += dx; y0 += sy_
    win.dirty = True


# ── Movement ───────────────────────────────────────────────────────────────

def forward(distance: float) -> None:
    _ensure_window()
    th = math.radians(_state["heading"])
    nx = _state["x"] + distance * math.cos(th)
    ny = _state["y"] + distance * math.sin(th)
    if _state["pen"]:
        sx0, sy0 = _to_screen(_state["x"], _state["y"])
        sx1, sy1 = _to_screen(nx, ny)
        _line_op(sx0, sy0, sx1, sy1, _state["pen_color"])
    _state["x"] = nx
    _state["y"] = ny
    _draw_turtle()


def back(distance: float) -> None:
    forward(-distance)


def right(degrees: float) -> None:
    _ensure_window()
    _state["heading"] = (_state["heading"] - degrees) % 360
    _draw_turtle()


def left(degrees: float) -> None:
    right(-degrees)


def goto(x: float, y: float) -> None:
    _ensure_window()
    if _state["pen"]:
        sx0, sy0 = _to_screen(_state["x"], _state["y"])
        sx1, sy1 = _to_screen(x, y)
        _line_op(sx0, sy0, sx1, sy1, _state["pen_color"])
    _state["x"] = x
    _state["y"] = y
    _draw_turtle()


def setheading(degrees: float) -> None:
    _ensure_window()
    _state["heading"] = degrees % 360
    _draw_turtle()


# ── Pen / colors ───────────────────────────────────────────────────────────

def pen_up() -> None:
    _state["pen"] = False


def pen_down() -> None:
    _state["pen"] = True


def color(r: int, g: int, b: int) -> None:
    _state["pen_color"] = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def bgcolor(r: int, g: int, b: int) -> None:
    _state["bg"] = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
    if _state["win"] is not None and not _state["win"]._closed:
        from kernel.gui.sdl2.surface import SDL_FillRect
        SDL_FillRect(_state["win"].surface, None, _state["bg"])
        _state["win"].dirty = True
        _draw_turtle()


# ── Utility ────────────────────────────────────────────────────────────────

def home() -> None:
    goto(0, 0)
    setheading(0)


def clear() -> None:
    if _state["win"] is None or _state["win"]._closed:
        return
    from kernel.gui.sdl2.surface import SDL_FillRect
    SDL_FillRect(_state["win"].surface, None, _state["bg"])
    _state["win"].dirty = True
    _draw_turtle()


def hide() -> None:
    _state["shown"] = False
    clear()
    # Re-replay last position-only marker (a no-op to reflect "no turtle")


def show() -> None:
    _state["shown"] = True
    _draw_turtle()


def position() -> tuple[float, float]:
    return (_state["x"], _state["y"])


def heading() -> float:
    return _state["heading"]


def is_down() -> bool:
    return _state["pen"]


def close() -> None:
    if _state["win"] is not None and not _state["win"]._closed:
        _state["win"].close()
    _state["win"] = None


# Short aliases that match Python's stdlib turtle.
fd = forward
bk = back
rt = right
lt = left
pu = pen_up
pd = pen_down

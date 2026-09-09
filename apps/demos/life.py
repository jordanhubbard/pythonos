"""apps.demos.life — Conway's Game of Life.

Classic CA on a fixed grid. Click to toggle a cell while the
simulation is running; ``space`` pauses; ``r`` reseeds with a
random pattern; ``c`` clears the board; ``ESC`` closes.
"""

import asyncio
import random

from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_Rect
from apps import registry


_W = 480
_H = 320
_CELL = 6
_COLS = _W // _CELL
_ROWS = _H // _CELL
_BG = 0x101820
_GRID = 0x182030
_ALIVE = 0x60D0FF


def _seed_random(grid: list[list[int]], density: float = 0.25) -> None:
    for r in range(_ROWS):
        for c in range(_COLS):
            grid[r][c] = 1 if random.random() < density else 0


def _step(grid: list[list[int]]) -> list[list[int]]:
    nxt = [[0] * _COLS for _ in range(_ROWS)]
    for r in range(_ROWS):
        for c in range(_COLS):
            n = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = (r + dr) % _ROWS, (c + dc) % _COLS
                    n += grid[rr][cc]
            if grid[r][c]:
                nxt[r][c] = 1 if n in (2, 3) else 0
            else:
                nxt[r][c] = 1 if n == 3 else 0
    return nxt


async def _run(win: CompositorWindow) -> None:
    grid = [[0] * _COLS for _ in range(_ROWS)]
    _seed_random(grid)

    state = {"closed": False, "paused": False}

    def _paint() -> None:
        SDL_FillRect(win.surface, None, _BG)
        for r in range(_ROWS):
            for c in range(_COLS):
                if grid[r][c]:
                    SDL_FillRect(win.surface,
                                 SDL_Rect(c * _CELL + 1, r * _CELL + 1,
                                          _CELL - 1, _CELL - 1),
                                 _ALIVE)
        win.dirty = True

    def on_event(ev) -> None:
        if ev.kind == _gui_input.EVENT_KEY_DOWN:
            if ev.code == _gui_input.KEY_ESC:
                state["closed"] = True
            elif ev.text == " ":
                state["paused"] = not state["paused"]
            elif ev.text in ("r", "R"):
                _seed_random(grid)
                _paint()
            elif ev.text in ("c", "C"):
                for row in grid:
                    for i in range(_COLS):
                        row[i] = 0
                _paint()
        elif ev.kind == _gui_input.MOUSE_DOWN:
            r, c = ev.y // _CELL, ev.x // _CELL
            if 0 <= r < _ROWS and 0 <= c < _COLS:
                grid[r][c] ^= 1
                _paint()

    win.set_event_handler(on_event)
    _paint()

    while not state["closed"] and not win._closed:
        if not state["paused"]:
            new_grid = _step(grid)
            for r in range(_ROWS):
                grid[r] = new_grid[r]
            _paint()
        await asyncio.sleep(1.0 / 8)

    win.close()


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Game of Life", x=160, y=120, w=_W, h=_H)
    compositor.add_window(win)
    await _run(win)


def life_icon():
    from apps._icons import _new_icon, _border, ICON_SIZE
    s = _new_icon(0x101820)
    _border(s, 0x60D0FF)
    # Glider pattern at icon scale
    for x, y in [(2, 1), (3, 2), (1, 3), (2, 3), (3, 3)]:
        SDL_FillRect(s, SDL_Rect(8 + x * 6, 12 + y * 6, 5, 5), 0x60D0FF)
    return s


registry.register(
    name="life",
    description="Conway's Game of Life (space pauses, r reseeds, c clears)",
    entry=main,
    icon_factory=life_icon,
    category="demo",
)

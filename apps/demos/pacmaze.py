"""apps.demos.pacmaze — INDEXED maze, pellets, four ghosts."""

from __future__ import annotations

from kernel.chipset import MODE_INDEXED, Move, View, Wait, blitter, paula
from kernel.gui import input as _gui_input
from apps import registry
from apps.arcade_logic import (
    TILE_PELLET,
    TILE_POWER,
    TILE_WALL,
    aabb,
    art_from_rows,
    default_pacmaze,
    eat_tile,
    ghost_step,
    orient_square_art,
    square_pcm,
    try_move,
)
from apps.chipset_play import run_view


PAC_OPEN_RIGHT = art_from_rows((
    "00222200",
    "02222220",
    "22222200",
    "22220000",
    "22222200",
    "02222220",
    "00222200",
    "00000000",
))
PAC_CLOSED = art_from_rows((
    "00222200",
    "02222220",
    "22222222",
    "22222222",
    "22222222",
    "02222220",
    "00222200",
    "00000000",
))
GHOST = art_from_rows((
    "00444400",
    "04444440",
    "40444044",
    "44444444",
    "44444444",
    "44044044",
    "40000004",
    "00000000",
))

_SCORE_FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "111", "100", "111"),
    "3": ("110", "001", "111", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "110"),
    "S": ("111", "100", "111", "001", "111"),
    "C": ("111", "100", "100", "100", "111"),
    "O": ("111", "101", "101", "101", "111"),
    "R": ("110", "101", "110", "101", "101"),
    "E": ("111", "100", "110", "100", "111"),
    "A": ("010", "101", "111", "101", "101"),
    "W": ("101", "101", "101", "111", "010"),
    "M": ("101", "111", "111", "101", "101"),
    "V": ("101", "101", "101", "101", "010"),
    "Q": ("111", "101", "101", "111", "001"),
    "X": ("101", "101", "010", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "/": ("001", "001", "010", "100", "100"),
}


def pac_frame(direction: str, mouth_open: bool) -> bytes:
    """Return the 8x8 player art oriented toward its travel direction."""
    source = PAC_OPEN_RIGHT if mouth_open else PAC_CLOSED
    return orient_square_art(source, 8, direction)


def _draw_score(view: View, score: int) -> None:
    text = "SCORE " + str(max(0, score)).rjust(6, "0")[-6:]
    width = len(text) * 4 - 1
    x0 = (view.width - width) // 2
    blitter.fill(view.pf0, x0 - 1, 1, width + 2, 5, 1)
    x = x0
    for char in text:
        glyph = _SCORE_FONT.get(char)
        if glyph is not None:
            for y, row in enumerate(glyph):
                for dx, bit in enumerate(row):
                    if bit == "1":
                        view.pf0.put(x + dx, 1 + y, 5)
        x += 4


def _paint_maze(view: View, maze) -> None:
    view.pf0.fill(0)
    t = maze.tile
    for r in range(maze.rows):
        for c in range(maze.cols):
            cell = maze.cells[r * maze.cols + c]
            x, y = c * t, r * t
            if cell == TILE_WALL:
                blitter.fill(view.pf0, x, y, t, t, 1)
            elif cell == TILE_PELLET:
                view.pf0.put(x + 3, y + 3, 2)
                view.pf0.put(x + 4, y + 3, 2)
            elif cell == TILE_POWER:
                blitter.fill(view.pf0, x + 2, y + 2, 4, 4, 3)


def _clear_cell(view: View, maze, px: int, py: int) -> None:
    t = maze.tile
    c, r = px // t, py // t
    blitter.fill(view.pf0, c * t, r * t, t, t, 0)


async def main(*args, **kwargs) -> None:
    maze = default_pacmaze()
    v = View(320, 200, mode=MODE_INDEXED, scale=3)
    v.palette[0] = 0x000010
    v.palette[1] = 0x2030C0
    v.palette[2] = 0xFFD0A0
    v.palette[3] = 0xFFFFFF
    v.palette[4] = 0xFF80C0
    v.palette[5] = 0xFFFF40
    v.copper.instructions = [
        Wait(0), Move("COLOR00", 0x000010),
    ]
    _paint_maze(v, maze)
    pac = v.sprites[0]
    pac.place(pac_frame("right", True), 8, 8,
              x=maze.start[0], y=maze.start[1], key_color=0)
    ghosts = []
    for i, pos in enumerate(maze.ghosts[:4]):
        spr = v.sprites[1 + i]
        spr.place(GHOST, 8, 8, x=pos[0], y=pos[1], key_color=0)
        ghosts.append({"spr": spr, "home": pos, "respawn": 0})
    chomp = paula.channel[0]
    chomp.sample = square_pcm(440, 40)
    chomp.rate = 8000
    chomp.volume = 22
    chomp.pan = 128
    chomp.loop = False
    power_left = 0
    ghost_clock = 0
    won = False
    dead = False
    score = 0
    direction = "right"
    animation_clock = 0
    demo_direction = _gui_input.KEY_RIGHT
    demo_active = False
    _draw_score(v, score)

    def tick(keys):
        nonlocal power_left, ghost_clock, won, dead
        nonlocal score, direction, animation_clock
        if dead and demo_active:
            dead = False
            pac.x, pac.y = maze.start
            direction = "right"
            for g in ghosts:
                g["spr"].x, g["spr"].y = g["home"]
                g["spr"].enabled = True
                g["respawn"] = 0
            v.copper.instructions = [Wait(0), Move("COLOR00", 0x000010)]
        if dead or won:
            return
        dx = dy = 0
        if _gui_input.KEY_LEFT in keys:
            dx = -2
            requested_direction = "left"
        elif _gui_input.KEY_RIGHT in keys:
            dx = 2
            requested_direction = "right"
        elif _gui_input.KEY_UP in keys:
            dy = -2
            requested_direction = "up"
        elif _gui_input.KEY_DOWN in keys:
            dy = 2
            requested_direction = "down"
        else:
            requested_direction = direction
        nx, ny = try_move(maze, pac.x, pac.y, dx, dy)
        moving = (nx, ny) != (pac.x, pac.y)
        pac.x, pac.y = nx, ny
        if moving:
            direction = requested_direction
            animation_clock += 1
        pac.pixels[:] = pac_frame(direction,
                                  moving and (animation_clock // 3) % 2 == 0)
        kind = eat_tile(maze, pac.x, pac.y)
        if kind is not None:
            _clear_cell(v, maze, pac.x, pac.y)
            score += 50 if kind == TILE_POWER else 10
            _draw_score(v, score)
            chomp.stop()
            chomp.play()
            if kind == TILE_POWER:
                power_left = 90
            if maze.pellets <= 0:
                won = True
                v.copper.instructions = [Wait(0), Move("COLOR00", 0x104010)]
        if power_left > 0:
            power_left -= 1
            v.palette[4] = 0x4060FF if (power_left // 4) % 2 == 0 else 0xFF80C0
        else:
            v.palette[4] = 0xFF80C0
        ghost_clock += 1
        if ghost_clock % 8 == 0:
            for g in ghosts:
                if g["respawn"] > 0:
                    continue
                gx, gy = ghost_step(maze, g["spr"].x, g["spr"].y, pac.x, pac.y)
                g["spr"].x, g["spr"].y = gx, gy
        for g in ghosts:
            if g["respawn"] > 0:
                g["respawn"] -= 1
                if g["respawn"] == 0:
                    g["spr"].x, g["spr"].y = g["home"]
                    g["spr"].enabled = True
                continue
            if not g["spr"].enabled:
                continue
            if aabb(pac.x, pac.y, 8, 8, g["spr"].x, g["spr"].y, 8, 8):
                if power_left > 0:
                    g["spr"].enabled = False
                    g["respawn"] = 90
                    score += 200
                    _draw_score(v, score)
                else:
                    dead = True
                    v.copper.instructions = [Wait(0), Move("COLOR00", 0x401010)]

    def on_exit():
        chomp.stop()

    def on_demo_toggle(enabled):
        nonlocal demo_active
        demo_active = enabled

    def demo(frame):
        nonlocal demo_direction
        if pac.x % maze.tile == 0 and pac.y % maze.tile == 0:
            start = (pac.x // maze.tile, pac.y // maze.tile)
            if power_left > 0:
                targets = {(g["spr"].x // maze.tile, g["spr"].y // maze.tile)
                           for g in ghosts if g["spr"].enabled}
            else:
                targets = {(i % maze.cols, i // maze.cols)
                           for i, cell in enumerate(maze.cells)
                           if cell == TILE_POWER}
                if not targets:
                    targets = {(i % maze.cols, i // maze.cols)
                               for i, cell in enumerate(maze.cells)
                               if cell == TILE_PELLET}

            # Breadth-first search follows actual corridors instead of getting
            # trapped by a Manhattan-distance choice on the far side of a wall.
            moves = (
                (_gui_input.KEY_LEFT, -1, 0),
                (_gui_input.KEY_RIGHT, 1, 0),
                (_gui_input.KEY_UP, 0, -1),
                (_gui_input.KEY_DOWN, 0, 1),
            )
            queue = [(start, None)]
            seen = {start}
            head = 0
            while head < len(queue):
                (cx, cy), first = queue[head]
                head += 1
                if (cx, cy) in targets and first is not None:
                    demo_direction = first
                    break
                for key, dx, dy in moves:
                    nxt = (cx + dx, cy + dy)
                    nx, ny = nxt
                    if (nxt in seen or nx < 0 or ny < 0
                            or nx >= maze.cols or ny >= maze.rows
                            or maze.cells[ny * maze.cols + nx] == TILE_WALL):
                        continue
                    seen.add(nxt)
                    queue.append((nxt, key if first is None else first))
        return {demo_direction}

    await run_view(v, tick, on_exit=on_exit, demo=demo,
                   controls="ARROWS MOVE", on_demo_toggle=on_demo_toggle)


from apps._icons import pacmaze_icon

registry.register(
    name="pacmaze",
    description="Pac-Maze — pellets, walls, four ghosts",
    entry=main,
    icon_factory=pacmaze_icon,
    category="game",
)

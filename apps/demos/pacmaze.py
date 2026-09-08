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
    square_pcm,
    try_move,
)
from apps.chipset_play import run_view


PAC = art_from_rows((
    "00222200",
    "02222220",
    "22222200",
    "22220000",
    "22222200",
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
    v.copper.instructions = [
        Wait(0), Move("COLOR00", 0x000010),
    ]
    _paint_maze(v, maze)
    pac = v.sprites[0]
    pac.place(PAC, 8, 8, x=maze.start[0], y=maze.start[1], key_color=0)
    ghosts = []
    for i, pos in enumerate(maze.ghosts[:4]):
        spr = v.sprites[1 + i]
        spr.place(GHOST, 8, 8, x=pos[0], y=pos[1], key_color=0)
        ghosts.append({"spr": spr, "home": pos})
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

    def tick(keys):
        nonlocal power_left, ghost_clock, won, dead
        if dead or won:
            return
        dx = dy = 0
        if _gui_input.KEY_LEFT in keys:
            dx = -2
        elif _gui_input.KEY_RIGHT in keys:
            dx = 2
        elif _gui_input.KEY_UP in keys:
            dy = -2
        elif _gui_input.KEY_DOWN in keys:
            dy = 2
        nx, ny = try_move(maze, pac.x, pac.y, dx, dy)
        pac.x, pac.y = nx, ny
        kind = eat_tile(maze, pac.x, pac.y)
        if kind is not None:
            _clear_cell(v, maze, pac.x, pac.y)
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
                gx, gy = ghost_step(maze, g["spr"].x, g["spr"].y, pac.x, pac.y)
                g["spr"].x, g["spr"].y = gx, gy
        for g in ghosts:
            if not g["spr"].enabled:
                continue
            if aabb(pac.x, pac.y, 8, 8, g["spr"].x, g["spr"].y, 8, 8):
                if power_left > 0:
                    g["spr"].x, g["spr"].y = g["home"]
                else:
                    dead = True
                    v.copper.instructions = [Wait(0), Move("COLOR00", 0x401010)]

    def on_exit():
        chomp.stop()

    await run_view(v, tick, on_exit=on_exit)


from apps._icons import pacmaze_icon

registry.register(
    name="pacmaze",
    description="Pac-Maze — pellets, walls, four ghosts",
    entry=main,
    icon_factory=pacmaze_icon,
    category="game",
)

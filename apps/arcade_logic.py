"""HAL-free arcade rules shared by Defender, Pac-Maze, and Raiders."""

from __future__ import annotations

from dataclasses import dataclass
import struct

TILE_EMPTY = 0
TILE_WALL = 1
TILE_PELLET = 2
TILE_POWER = 3


@dataclass
class Maze:
    cols: int
    rows: int
    tile: int
    cells: list
    start: tuple
    ghosts: list
    pellets: int

    def tile_at(self, px: int, py: int) -> int:
        col = px // self.tile
        row = py // self.tile
        if row < 0 or col < 0 or row >= self.rows or col >= self.cols:
            return TILE_WALL
        return self.cells[row * self.cols + col]

    def set_tile(self, px: int, py: int, value: int) -> None:
        col = px // self.tile
        row = py // self.tile
        if 0 <= row < self.rows and 0 <= col < self.cols:
            self.cells[row * self.cols + col] = value


def parse_maze(rows, tile: int = 8) -> Maze:
    lines = [str(row) for row in rows]
    cols = len(lines[0])
    rows_n = len(lines)
    cells: list[int] = []
    start = (tile, tile)
    ghosts: list[tuple[int, int]] = []
    pellets = 0
    for r, line in enumerate(lines):
        for c, ch in enumerate(line[:cols]):
            if ch == "#":
                cells.append(TILE_WALL)
            elif ch == ".":
                cells.append(TILE_PELLET)
                pellets += 1
            elif ch == "o":
                cells.append(TILE_POWER)
            elif ch == "P":
                cells.append(TILE_EMPTY)
                start = (c * tile, r * tile)
            elif ch == "G":
                cells.append(TILE_EMPTY)
                ghosts.append((c * tile, r * tile))
            else:
                cells.append(TILE_EMPTY)
        extra = cols - len(line[:cols])
        for _ in range(extra):
            cells.append(TILE_EMPTY)
    return Maze(cols=cols, rows=rows_n, tile=tile, cells=cells,
                start=start, ghosts=ghosts, pellets=pellets)


def try_move(maze: Maze, x: int, y: int, dx: int, dy: int) -> tuple[int, int]:
    nx, ny = x + dx, y + dy
    if maze.tile_at(nx, ny) == TILE_WALL:
        return x, y
    return nx, ny


def eat_tile(maze: Maze, x: int, y: int):
    kind = maze.tile_at(x, y)
    if kind == TILE_PELLET:
        maze.pellets -= 1
        maze.set_tile(x, y, TILE_EMPTY)
        return kind
    if kind == TILE_POWER:
        maze.set_tile(x, y, TILE_EMPTY)
        return kind
    return None


def ghost_step(maze: Maze, gx: int, gy: int, px: int, py: int) -> tuple[int, int]:
    if maze.tile_at(gx, gy) == TILE_WALL:
        return gx, gy
    t = maze.tile
    options = []
    for dx, dy in ((-t, 0), (t, 0), (0, -t), (0, t)):
        nx, ny = try_move(maze, gx, gy, dx, dy)
        if (nx, ny) != (gx, gy):
            options.append((nx, ny))
    if not options:
        return gx, gy

    def dist(pos):
        return abs(pos[0] - px) + abs(pos[1] - py)

    return min(options, key=dist)


def scroll_wrap(pos: int, delta: int, width: int) -> int:
    return (pos + delta) % width


def aabb(ax: int, ay: int, aw: int, ah: int,
         bx: int, by: int, bw: int, bh: int) -> bool:
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def formation_xy(i: int, t: int, origin_x: int, origin_y: int,
                 spacing: int = 24) -> tuple[int, int]:
    col = i % 4
    row = i // 4
    phase = (t // 8) % 4
    bob = 0
    if phase == 1:
        bob = 2
    elif phase == 3:
        bob = -2
    return origin_x + col * spacing + bob, origin_y + row * 16


def default_pacmaze(tile: int = 8, cols: int = 40, rows: int = 25) -> Maze:
    grid = [[" "] * cols for _ in range(rows)]
    for c in range(cols):
        grid[0][c] = "#"
        grid[rows - 1][c] = "#"
    for r in range(rows):
        grid[r][0] = "#"
        grid[r][cols - 1] = "#"
    for r in range(4, rows - 1, 4):
        for c in range(2, cols - 2):
            if c % 8 not in (0, 1):
                grid[r][c] = "#"
    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            if grid[r][c] == " ":
                grid[r][c] = "."
    grid[1][1] = "P"
    grid[1][cols - 2] = "G"
    grid[rows - 2][1] = "G"
    grid[rows - 2][cols - 2] = "G"
    grid[rows // 2][cols // 2] = "G"
    grid[3][3] = "o"
    grid[3][cols - 4] = "o"
    return parse_maze(["".join(row) for row in grid], tile=tile)


def art_from_rows(rows) -> bytes:
    out = bytearray()
    for row in rows:
        for ch in row:
            out.append(int(ch) if ch.isdigit() else 0)
    return bytes(out)


def square_pcm(freq: int, ms: int, rate: int = 8000, amp: int = 10000) -> bytes:
    n = rate * ms // 1000
    half = max(1, rate // (freq * 2))
    out = bytearray()
    for i in range(n):
        s = amp if (i // half) % 2 == 0 else -amp
        out += struct.pack("<h", s)
    return bytes(out)


def mountain_height(x: int, world_w: int = 320) -> int:
    """Repeating hills for Defender terrain, 0–40 px above a baseline."""
    t = x % world_w
    h = 12
    h += 18 if (t // 40) % 2 == 0 else 6
    h += (t % 17) // 3
    return h

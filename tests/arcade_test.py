#!/usr/bin/env python3
"""Host-side arcade rule tests. No QEMU, no _hal.

Run: python3 tests/arcade_test.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_failed = 0
_passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global _failed, _passed
    ok = bool(cond)
    if ok:
        _passed += 1
        print(f"  PASS  {name}" + (f" ({detail})" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("arcade_test")
    from apps.arcade_logic import (
        TILE_EMPTY,
        TILE_PELLET,
        TILE_POWER,
        TILE_WALL,
        aabb,
        eat_tile,
        formation_xy,
        ghost_step,
        mountain_height,
        parse_maze,
        scroll_wrap,
        try_move,
        default_pacmaze,
    )

    maze_rows = [
        "#####",
        "#P.G#",
        "# o #",
        "#####",
    ]
    maze = parse_maze(maze_rows, tile=8)
    check("parse start", maze.start == (8, 8), str(maze.start))
    check("parse one ghost", maze.ghosts == [(24, 8)], str(maze.ghosts))
    check("parse pellet count", maze.pellets == 1, str(maze.pellets))
    check("wall at origin", maze.tile_at(0, 0) == TILE_WALL)
    check("pellet east of start", maze.tile_at(16, 8) == TILE_PELLET)
    check("power pellet", maze.tile_at(16, 16) == TILE_POWER)

    nx, ny = try_move(maze, 8, 8, -8, 0)
    check("try_move blocked by wall", (nx, ny) == (8, 8))
    nx, ny = try_move(maze, 8, 8, 8, 0)
    check("try_move into pellet", (nx, ny) == (16, 8))

    kind = eat_tile(maze, 16, 8)
    check("eat pellet kind", kind == TILE_PELLET)
    check("eat pellet decrements", maze.pellets == 0)
    check("eaten tile empty", maze.tile_at(16, 8) == TILE_EMPTY)
    check("eat empty is none", eat_tile(maze, 16, 8) is None)

    gx, gy = ghost_step(maze, 24, 8, 8, 8)
    check("ghost steps toward player", gx < 24, f"{gx},{gy}")
    gx2, gy2 = ghost_step(maze, 0, 0, 8, 8)
    check("ghost in wall stays", (gx2, gy2) == (0, 0))

    check("scroll wrap positive", scroll_wrap(318, 4, 320) == 2)
    check("scroll wrap negative", scroll_wrap(2, -4, 320) == 318)

    check("aabb overlap", aabb(0, 0, 8, 8, 4, 4, 8, 8))
    check("aabb miss", not aabb(0, 0, 8, 8, 20, 20, 8, 8))

    x0, y0 = formation_xy(0, t=0, origin_x=40, origin_y=20, spacing=24)
    x1, y1 = formation_xy(1, t=0, origin_x=40, origin_y=20, spacing=24)
    check("formation slot 0 at origin", (x0, y0) == (40, 20), f"{x0},{y0}")
    check("formation slot 1 spaced", x1 == 64 and y1 == 20, f"{x1},{y1}")
    x_bob, _ = formation_xy(0, t=8, origin_x=40, origin_y=20, spacing=24)
    check("formation bobs", x_bob != 40, str(x_bob))

    maze2 = default_pacmaze()
    check("default maze has pellets", maze2.pellets > 10, str(maze2.pellets))
    check("default maze has four ghosts", len(maze2.ghosts) == 4, str(len(maze2.ghosts)))
    check("mountain height in range", 12 <= mountain_height(0) <= 40)

    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())

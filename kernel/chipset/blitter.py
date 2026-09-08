"""Software blitter: fill, copy, cookie-cut. Clips; never raises."""


def fill(dest, x: int, y: int, w: int, h: int, color: int) -> None:
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(dest.width, x + w)
    y2 = min(dest.height, y + h)
    for py in range(y1, y2):
        for px in range(x1, x2):
            dest.put(px, py, color)


def copy(src, dest, sx: int, sy: int, dx: int, dy: int,
         w: int, h: int) -> None:
    for j in range(h):
        for i in range(w):
            dest.put(dx + i, dy + j, src.get(sx + i, sy + j))


def cookie(src, mask, dest, sx: int, sy: int, dx: int, dy: int,
           w: int, h: int) -> None:
    """Where mask is non-zero, write src; otherwise leave dest."""
    for j in range(h):
        for i in range(w):
            if mask.get(sx + i, sy + j) == 0:
                continue
            dest.put(dx + i, dy + j, src.get(sx + i, sy + j))

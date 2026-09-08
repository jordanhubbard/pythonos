"""Playfield bitmap — INDEXED (1 byte/pixel) or DIRECT (BGRX)."""

MODE_DIRECT = "direct"
MODE_INDEXED = "indexed"


def pack_bgrx(color: int) -> bytes:
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return bytes((b, g, r, 0xFF))


def unpack_bgrx(buf, offset: int) -> int:
    b = buf[offset]
    g = buf[offset + 1]
    r = buf[offset + 2]
    return (r << 16) | (g << 8) | b


class Playfield:
    """One bitplane/playfield. ``color`` for INDEXED is a palette index;
    for DIRECT it is 0xRRGGBB."""

    def __init__(self, width: int, height: int, mode: str = MODE_DIRECT) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("playfield size must be positive")
        self.width = width
        self.height = height
        self.mode = mode
        self.scroll_x = 0
        self.scroll_y = 0
        if mode == MODE_INDEXED:
            self.pixels = bytearray(width * height)
        else:
            self.pixels = bytearray(width * height * 4)

    def _bounded(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def put(self, x: int, y: int, color: int) -> None:
        if not self._bounded(x, y):
            return
        if self.mode == MODE_INDEXED:
            self.pixels[y * self.width + x] = color & 0xFF
            return
        o = (y * self.width + x) * 4
        self.pixels[o:o + 4] = pack_bgrx(color)

    def get(self, x: int, y: int) -> int:
        if not self._bounded(x, y):
            return 0
        if self.mode == MODE_INDEXED:
            return self.pixels[y * self.width + x]
        return unpack_bgrx(self.pixels, (y * self.width + x) * 4)

    def sample(self, x: int, y: int) -> int:
        """Get with wrap scroll."""
        sx = (x + self.scroll_x) % self.width
        sy = (y + self.scroll_y) % self.height
        return self.get(sx, sy)

    def fill(self, color: int) -> None:
        if self.mode == MODE_INDEXED:
            v = color & 0xFF
            self.pixels = bytearray([v]) * (self.width * self.height)
            return
        pix = pack_bgrx(color)
        self.pixels = bytearray(pix) * (self.width * self.height)

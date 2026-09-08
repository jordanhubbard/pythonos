"""Hardware sprite slot. Transparent on key_color (INDEXED default 0)."""

from kernel.chipset.playfield import unpack_bgrx


class Sprite:
    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.w = 0
        self.h = 0
        self.enabled = False
        self.key_color = 0
        self.pixels = bytearray()
        self.indexed = True

    def place(self, pixels, w: int, h: int, x: int = 0, y: int = 0,
              key_color: int = 0) -> None:
        self.w = w
        self.h = h
        self.x = x
        self.y = y
        self.key_color = key_color
        self.enabled = True
        data = bytes(pixels)
        if len(data) == w * h:
            self.indexed = True
            self.pixels = bytearray(data)
        elif len(data) == w * h * 4:
            self.indexed = False
            self.pixels = bytearray(data)
        else:
            raise ValueError("sprite pixel length must be w*h or w*h*4")

    def pixel_at(self, px: int, py: int, palette) -> int | None:
        """Return 0xRRGGBB or None if transparent / out of sprite."""
        if not self.enabled or self.w <= 0:
            return None
        lx = px - self.x
        ly = py - self.y
        if lx < 0 or ly < 0 or lx >= self.w or ly >= self.h:
            return None
        if self.indexed:
            idx = self.pixels[ly * self.w + lx]
            if idx == (self.key_color & 0xFF):
                return None
            if palette is None:
                return idx
            if 0 <= idx < len(palette):
                return palette[idx] & 0xFFFFFF
            return 0
        color = unpack_bgrx(self.pixels, (ly * self.w + lx) * 4)
        if color == (self.key_color & 0xFFFFFF):
            return None
        return color

"""A View is copper + two playfields + eight sprites + palette."""

from kernel.chipset.copper import Copper
from kernel.chipset.playfield import MODE_DIRECT, Playfield
from kernel.chipset.sprite import Sprite


class View:
    def __init__(self, width: int, height: int, mode: str = MODE_DIRECT,
                 scale: int = 1) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("view size must be positive")
        self.width = width
        self.height = height
        self.mode = mode
        self.scale = max(1, int(scale))
        self.pf0 = Playfield(width, height, mode)
        self.pf1 = Playfield(width, height, mode)
        self.sprites = [Sprite() for _ in range(8)]
        self.copper = Copper()
        self.palette = [0] * 32
        self.bplcon = 0
        self.key_color = 0
        self.diw_start = 0
        self.diw_stop = height - 1

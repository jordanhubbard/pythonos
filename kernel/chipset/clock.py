"""FrameClock — 30 Hz vblank lives on Chipset.start / tick / vblank.

This module exists so the chipset layout matches the spec. Callers
should keep using ``chipset.start()`` and ``chipset.tick()``.
"""

TICK_HZ = 30

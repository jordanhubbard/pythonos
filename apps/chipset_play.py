"""LoadView game loop shared by chipset arcade demos."""

from __future__ import annotations

import asyncio

from kernel.chipset import chipset
from kernel.gui import input as _gui_input


async def run_view(view, tick, on_space=None, on_exit=None) -> None:
    """Load ``view``, call ``tick(keys)`` at 30 Hz until ESC."""
    prev = chipset.active_view
    chipset.load_view(view)
    keys = set()
    closed = False

    def on_event(ev):
        nonlocal closed
        if ev.kind == _gui_input.KEY_DOWN:
            if ev.code == _gui_input.KEY_ESC:
                closed = True
            elif ev.code == _gui_input.KEY_SPACE and on_space is not None:
                on_space()
            keys.add(ev.code)
        elif ev.kind == _gui_input.KEY_UP:
            keys.discard(ev.code)

    chipset.on_event = on_event
    while not closed:
        tick(keys)
        await asyncio.sleep(1.0 / 30)
    chipset.on_event = None
    if on_exit is not None:
        on_exit()
    if chipset.workbench is not None:
        chipset.load_view(chipset.workbench)
    elif prev is not None:
        chipset.load_view(prev)

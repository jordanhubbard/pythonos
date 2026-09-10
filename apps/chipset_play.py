"""LoadView game loop shared by chipset arcade demos."""

from __future__ import annotations

import asyncio

from kernel.chipset import chipset
from kernel.gui import input as _gui_input


_FONT = {
    "A": ("010", "101", "111", "101", "101"),
    "C": ("111", "100", "100", "100", "111"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "I": ("111", "010", "010", "010", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("111", "101", "101", "111", "001"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "101", "111", "010"),
    "X": ("101", "101", "010", "101", "101"),
    "/": ("001", "001", "010", "100", "100"),
    ":": ("000", "010", "000", "010", "000"),
}


def _guide_art(text: str, fg: int = 5, bg: int = 7) -> tuple[bytes, int]:
    width = len(text) * 4 - 1
    pixels = bytearray([bg]) * (width * 7)
    for ci, char in enumerate(text):
        glyph = _FONT.get(char)
        if glyph is None:
            continue
        x0 = ci * 4
        for y, row in enumerate(glyph):
            for x, bit in enumerate(row):
                if bit == "1":
                    pixels[(y + 1) * width + x0 + x] = fg
    return bytes(pixels), width


def _set_guide(view, controls: str, demo_enabled: bool) -> None:
    text = (controls + "  D DEMO:" + ("ON" if demo_enabled else "OFF")
            + "  ESC/Q EXIT")
    art, width = _guide_art(text)
    if view.palette[5] == 0:
        view.palette[5] = 0xFFFFFF
    view.palette[7] = 0x000000
    view.sprites[7].place(art, width, 7,
                          x=(view.width - width) // 2,
                          y=view.height - 7, key_color=0)


async def run_view(view, tick, on_space=None, on_exit=None, demo=None,
                   controls: str = "ARROWS MOVE", on_demo_toggle=None) -> None:
    """Load ``view``, call ``tick(keys)`` at 30 Hz until ESC."""
    prev = chipset.active_view
    chipset.load_view(view)
    chipset.start()
    keys = set()
    closed = False
    demo_enabled = False
    frame = 0
    _set_guide(view, controls, demo_enabled)

    def on_event(ev):
        nonlocal closed, demo_enabled
        if ev.kind == _gui_input.QUIT:
            closed = True
            return
        if ev.kind == _gui_input.EVENT_KEY_DOWN:
            if (ev.code == _gui_input.KEY_ESC
                    or ev.text in ("q", "Q")
                    or ev.code in (ord("q"), ord("Q"))):
                closed = True
            elif (ev.code in (ord("d"), ord("D"))
                    and ev.code not in keys and demo is not None):
                demo_enabled = not demo_enabled
                keys.clear()
                _set_guide(view, controls, demo_enabled)
                if on_demo_toggle is not None:
                    on_demo_toggle(demo_enabled)
            elif ev.code == _gui_input.KEY_SPACE and on_space is not None:
                on_space()
            keys.add(ev.code)
        elif ev.kind == _gui_input.EVENT_KEY_UP:
            keys.discard(ev.code)

    chipset.on_event = on_event
    while not closed and not chipset.exit_requested:
        active_keys = set(demo(frame)) if demo_enabled and demo is not None else keys
        if (demo_enabled and on_space is not None
                and _gui_input.KEY_SPACE in active_keys):
            on_space()
        tick(active_keys)
        frame += 1
        await asyncio.sleep(1.0 / 30)
    chipset.on_event = None
    if on_exit is not None:
        on_exit()
    if chipset.workbench is not None:
        chipset.load_view(chipset.workbench)
    elif prev is not None:
        chipset.load_view(prev)
    chipset.stop()
    try:
        from kernel.gui.compositor import compositor
        compositor._bridge_needs_redraw = True
    except Exception:
        pass

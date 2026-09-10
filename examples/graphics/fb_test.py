"""
Framebuffer drawing demo. Draws color bands and a centered banner.

Run from the kernel REPL after booting in GUI mode (`make run-gui`):

    >>> sh('fb_test')

Exits silently with a message on serial-only boots (no framebuffer).
"""

from kernel.display import framebuffer as _fb_mod


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def main(argv=None, cwd="/", read_char=None, write=None):
    fb = _fb_mod.fb
    if fb is None:
        _line(write, "fb_test: no framebuffer (boot in GUI mode: make run-gui)")
        return

    bands = [
        _fb_mod.RED, _fb_mod.YELLOW, _fb_mod.GREEN,
        _fb_mod.CYAN, _fb_mod.BLUE, _fb_mod.MAGENTA,
    ]
    band_h = fb.height // len(bands)
    for i, color in enumerate(bands):
        fb.fill_rect(0, i * band_h, fb.width, band_h, color)

    msg = "PythonOS GUI"
    char_w = 8
    px = (fb.width - len(msg) * char_w) // 2
    py = fb.height // 2 - 4
    pad = 6
    fb.fill_rect(px - pad, py - pad,
                 len(msg) * char_w + 2 * pad, 8 + 2 * pad,
                 _fb_mod.BLACK)
    fb.draw_text(px, py, msg, fg=_fb_mod.WHITE)

    _line(write, f"fb_test: drew {fb.width}x{fb.height}x{fb.bpp} test pattern")

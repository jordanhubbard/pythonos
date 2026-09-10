"""Path: /examples/graphics/sdl/sdl_hello.py

Minimal SDL-compatible window example: initialize video, create a window,
fill its surface, present it, and release resources in reverse order.
Works on any boot that has a framebuffer up — that's the only
graphics surface we have, regardless of -display sdl vs -display none.
"""

import sdl2


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def main(argv=None, cwd="/", read_char=None, write=None):
    sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_EVENTS)
    win = sdl2.SDL_CreateWindow(b"Hello PythonOS", 0, 0, 320, 200)
    surf = sdl2.SDL_GetWindowSurface(win)
    blue = sdl2.SDL_MapRGB(surf.format, 0, 0, 255)
    sdl2.SDL_FillRect(surf, None, blue)
    rc = sdl2.SDL_UpdateWindowSurface(win)

    _line(write, "sdl_hello: window=%dx%d, blue=%#x, present=%d"
                  % (win.w, win.h, blue, rc))

    # Drain whatever pending events arrived, just to exercise the path.
    ev = sdl2.SDL_Event()
    drained = 0
    while sdl2.SDL_PollEvent(ev):
        drained += 1
    _line(write, "sdl_hello: drained %d events" % drained)

    sdl2.SDL_DestroyWindow(win)
    sdl2.SDL_Quit()
    _line(write, "sdl_hello: ok")

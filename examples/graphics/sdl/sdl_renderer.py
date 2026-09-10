"""Path: /examples/graphics/sdl/sdl_renderer.py

Software-renderer example. It opens a window, creates a renderer, fills red, draws a green
rectangle, presents, and exits cleanly. Verifies the PySDL2-shaped
SDL_CreateRenderer/SetRenderDrawColor/RenderClear/RenderFillRect/
RenderPresent path works end-to-end.
"""

import sdl2


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def main(argv=None, cwd="/", read_char=None, write=None):
    sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
    win = sdl2.SDL_CreateWindow(b"Renderer", 0, 0, 320, 200)
    rdr = sdl2.SDL_CreateRenderer(win, -1, sdl2.SDL_RENDERER_SOFTWARE)

    sdl2.SDL_SetRenderDrawColor(rdr, 255, 0, 0, 255)
    sdl2.SDL_RenderClear(rdr)

    sdl2.SDL_SetRenderDrawColor(rdr, 0, 255, 0, 255)
    sdl2.SDL_RenderFillRect(rdr, sdl2.SDL_Rect(80, 60, 160, 80))

    sdl2.SDL_RenderPresent(rdr)
    _line(write, "sdl_renderer: presented red bg + green rect")

    sdl2.SDL_DestroyRenderer(rdr)
    sdl2.SDL_DestroyWindow(win)
    sdl2.SDL_Quit()
    _line(write, "sdl_renderer: ok")

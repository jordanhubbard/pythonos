"""sdl_text — bitmap-font rendering example through the TTF-shaped API.

Verifies the sdl2.sdlttf surface: open a font, render "PythonOS" to a
surface, blit it into a window via the renderer, present, exit.
"""

import sdl2


def _line(write, text=""):
    if write:
        write(text + "\n")
    else:
        print(text)


async def main(argv=None, cwd="/", read_char=None, write=None):
    sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
    sdl2.TTF_Init()

    win = sdl2.SDL_CreateWindow(b"Text", 0, 0, 320, 64)
    rdr = sdl2.SDL_CreateRenderer(win, -1, sdl2.SDL_RENDERER_SOFTWARE)

    sdl2.SDL_SetRenderDrawColor(rdr, 20, 20, 60, 255)
    sdl2.SDL_RenderClear(rdr)

    # Path is informational — our v0 renderer uses the bundled bitmap font.
    font = sdl2.TTF_OpenFont(b"<builtin>", 16)
    label = sdl2.TTF_RenderText_Blended(font, b"PythonOS", sdl2.SDL_Color(255, 255, 255, 255))
    _line(write, "sdl_text: rendered surface %dx%d" % (label.w, label.h))

    tex = sdl2.SDL_CreateTextureFromSurface(rdr, label)
    sdl2.SDL_RenderCopy(rdr, tex, None, sdl2.SDL_Rect(20, 20, label.w, label.h))
    sdl2.SDL_RenderPresent(rdr)

    sdl2.TTF_CloseFont(font)
    sdl2.TTF_Quit()
    sdl2.SDL_DestroyRenderer(rdr)
    sdl2.SDL_DestroyWindow(win)
    sdl2.SDL_Quit()
    _line(write, "sdl_text: ok")

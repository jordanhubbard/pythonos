"""
sdl2 — PySDL2-compatible API surface for PythonOS.

This package mirrors the public PySDL2 namespace closely enough that the
canonical samples from the project's compatibility corpus
(``examples/graphics/sdl/*.py``) run unchanged. It is *not* a ctypes
wrapper around libSDL2 — there is no libSDL2 inside the guest. Instead,
the API thunks into kernel.gui.compositor, kernel.gui.input, and
kernel.sound.mixer.

The module is frozen as the top-level ``sdl2`` (see freeze_kernel.py
which strips the ``kernel.gui.`` prefix for any module under
``kernel/gui/sdl2``)... actually we publish under ``kernel.gui.sdl2``
and ALSO register ``sdl2`` as an alias so unmodified PySDL2 source
``import sdl2`` works.
"""

import sys as _sys

# ── Subsystem flags ─────────────────────────────────────────────────────────

SDL_INIT_TIMER          = 0x00000001
SDL_INIT_AUDIO          = 0x00000010
SDL_INIT_VIDEO          = 0x00000020
SDL_INIT_JOYSTICK       = 0x00000200
SDL_INIT_HAPTIC         = 0x00001000
SDL_INIT_GAMECONTROLLER = 0x00002000
SDL_INIT_EVENTS         = 0x00004000
SDL_INIT_EVERYTHING     = (SDL_INIT_TIMER | SDL_INIT_AUDIO | SDL_INIT_VIDEO |
                           SDL_INIT_EVENTS)


_initialized = 0


def SDL_Init(flags: int = SDL_INIT_EVERYTHING) -> int:
    """Initialize the requested SDL subsystems. Returns 0 on success."""
    global _initialized
    _initialized |= flags

    # Make sure the input EventQueue exists so SDL_PollEvent can drain it
    # immediately even if the application calls it before any event arrives.
    if flags & SDL_INIT_EVENTS or flags & SDL_INIT_VIDEO:
        from kernel.gui import input as _gui_input
        _gui_input.init()
    return 0


def SDL_Quit() -> None:
    global _initialized
    _initialized = 0


def SDL_WasInit(flags: int) -> int:
    return _initialized & flags


# ── Re-export the submodule surface at package level (PySDL2 style) ────────

from kernel.gui.sdl2 import video as _video    # noqa: E402
from kernel.gui.sdl2 import surface as _surface  # noqa: E402
from kernel.gui.sdl2 import events as _events   # noqa: E402
from kernel.gui.sdl2 import render as _render   # noqa: E402
from kernel.gui.sdl2 import sdlmixer as _mixer  # noqa: E402
from kernel.gui.sdl2 import sdlttf as _ttf      # noqa: E402

# Window / video
SDL_WINDOWPOS_UNDEFINED = _video.SDL_WINDOWPOS_UNDEFINED
SDL_WINDOWPOS_CENTERED  = _video.SDL_WINDOWPOS_CENTERED
SDL_WINDOW_SHOWN        = _video.SDL_WINDOW_SHOWN
SDL_WINDOW_HIDDEN       = _video.SDL_WINDOW_HIDDEN
SDL_WINDOW_RESIZABLE    = _video.SDL_WINDOW_RESIZABLE
SDL_CreateWindow        = _video.SDL_CreateWindow
SDL_DestroyWindow       = _video.SDL_DestroyWindow
SDL_GetWindowSurface    = _video.SDL_GetWindowSurface
SDL_UpdateWindowSurface = _video.SDL_UpdateWindowSurface
SDL_SetWindowTitle      = _video.SDL_SetWindowTitle
SDL_Window              = _video.SDL_Window

# Surface / pixel format
SDL_Surface             = _surface.SDL_Surface
SDL_PixelFormat         = _surface.SDL_PixelFormat
SDL_Rect                = _surface.SDL_Rect
SDL_Color               = _surface.SDL_Color
SDL_Point               = _surface.SDL_Point
SDL_FillRect            = _surface.SDL_FillRect
SDL_MapRGB              = _surface.SDL_MapRGB
SDL_MapRGBA             = _surface.SDL_MapRGBA
SDL_FreeSurface         = _surface.SDL_FreeSurface
SDL_BlitSurface         = _surface.SDL_BlitSurface
SDL_LoadBMP             = _surface.SDL_LoadBMP

# Renderer
SDL_Renderer            = _render.SDL_Renderer
SDL_Texture             = _render.SDL_Texture
SDL_RENDERER_SOFTWARE   = _render.SDL_RENDERER_SOFTWARE
SDL_RENDERER_ACCELERATED = _render.SDL_RENDERER_ACCELERATED
SDL_RENDERER_PRESENTVSYNC = _render.SDL_RENDERER_PRESENTVSYNC
SDL_CreateRenderer      = _render.SDL_CreateRenderer
SDL_DestroyRenderer     = _render.SDL_DestroyRenderer
SDL_SetRenderDrawColor  = _render.SDL_SetRenderDrawColor
SDL_RenderClear         = _render.SDL_RenderClear
SDL_RenderFillRect      = _render.SDL_RenderFillRect
SDL_RenderDrawRect      = _render.SDL_RenderDrawRect
SDL_RenderCopy          = _render.SDL_RenderCopy
SDL_RenderPresent       = _render.SDL_RenderPresent
SDL_CreateTextureFromSurface = _render.SDL_CreateTextureFromSurface
SDL_DestroyTexture      = _render.SDL_DestroyTexture

# Events
SDL_Event               = _events.SDL_Event
SDL_PollEvent           = _events.SDL_PollEvent
SDL_WaitEvent           = _events.SDL_WaitEvent
SDL_PumpEvents          = _events.SDL_PumpEvents
SDL_QUIT                = _events.SDL_QUIT
SDL_KEYDOWN             = _events.SDL_KEYDOWN
SDL_KEYUP               = _events.SDL_KEYUP
SDL_MOUSEMOTION         = _events.SDL_MOUSEMOTION
SDL_MOUSEBUTTONDOWN     = _events.SDL_MOUSEBUTTONDOWN
SDL_MOUSEBUTTONUP       = _events.SDL_MOUSEBUTTONUP
SDL_WINDOWEVENT         = _events.SDL_WINDOWEVENT
# Common keysyms (ASCII for printables; arbitrary high ints for specials)
SDLK_RETURN             = _events.SDLK_RETURN
SDLK_ESCAPE             = _events.SDLK_ESCAPE
SDLK_BACKSPACE          = _events.SDLK_BACKSPACE
SDLK_TAB                = _events.SDLK_TAB
SDLK_SPACE              = _events.SDLK_SPACE
SDLK_UP                 = _events.SDLK_UP
SDLK_DOWN               = _events.SDLK_DOWN
SDLK_LEFT               = _events.SDLK_LEFT
SDLK_RIGHT              = _events.SDLK_RIGHT
SDLK_F1                 = _events.SDLK_F1
SDLK_F2                 = _events.SDLK_F2
SDLK_LSHIFT             = _events.SDLK_LSHIFT
SDLK_RSHIFT             = _events.SDLK_RSHIFT
SDLK_LCTRL              = _events.SDLK_LCTRL
SDLK_LALT               = _events.SDLK_LALT
KMOD_NONE               = _events.KMOD_NONE
KMOD_LSHIFT             = _events.KMOD_LSHIFT
KMOD_RSHIFT             = _events.KMOD_RSHIFT
KMOD_SHIFT              = _events.KMOD_SHIFT
KMOD_LCTRL              = _events.KMOD_LCTRL
KMOD_CTRL               = _events.KMOD_CTRL
KMOD_LALT               = _events.KMOD_LALT
KMOD_ALT                = _events.KMOD_ALT

# Mixer (compat alias namespace)
# TTF
TTF_Init                = _ttf.TTF_Init
TTF_Quit                = _ttf.TTF_Quit
TTF_OpenFont            = _ttf.TTF_OpenFont
TTF_CloseFont           = _ttf.TTF_CloseFont
TTF_RenderText_Blended  = _ttf.TTF_RenderText_Blended
TTF_RenderText_Solid    = _ttf.TTF_RenderText_Solid
TTF_SizeText            = _ttf.TTF_SizeText
TTF_Font                = _ttf.TTF_Font

# Mixer (compat alias namespace)
Mix_OpenAudio           = _mixer.Mix_OpenAudio
Mix_CloseAudio          = _mixer.Mix_CloseAudio
Mix_LoadWAV             = _mixer.Mix_LoadWAV
Mix_PlayChannel         = _mixer.Mix_PlayChannel
Mix_FreeChunk           = _mixer.Mix_FreeChunk
MIX_DEFAULT_FREQUENCY   = _mixer.MIX_DEFAULT_FREQUENCY
MIX_DEFAULT_FORMAT      = _mixer.MIX_DEFAULT_FORMAT
MIX_DEFAULT_CHANNELS    = _mixer.MIX_DEFAULT_CHANNELS

# Timing
def SDL_GetTicks() -> int:
    """Milliseconds since boot; coarse 10ms granularity (PIT)."""
    import _hal
    # _hal exposes a tick counter via the kernel timer
    return int((getattr(_hal, "_pit_ticks", 0) or 0) * 10)


def SDL_Delay(ms: int) -> None:
    """Synchronous-ish delay using the asyncio loop. Compatible callers
    typically use this from inside an async context anyway."""
    import time
    end = SDL_GetTicks() + ms
    while SDL_GetTicks() < end:
        pass


# ── Top-level alias so ``import sdl2`` works from user code ──────────────

# Register self as the bare 'sdl2' name in sys.modules so ``import sdl2``
# resolves to this module — PySDL2 source needs the unprefixed name.
_sys.modules.setdefault("sdl2", _sys.modules[__name__])

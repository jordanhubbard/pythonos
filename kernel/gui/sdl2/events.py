"""sdl2.events — Event polling + keysym/modifier constants.

Drains :data:`kernel.gui.input.queue` and translates each :class:`Event`
into a PySDL2-shaped ``SDL_Event`` record.
"""

import asyncio
from dataclasses import dataclass, field

from kernel.gui import input as _gui_input


# ── Event type codes (SDL2 numeric values) ──────────────────────────────────

SDL_FIRSTEVENT       = 0
SDL_QUIT             = 0x100
SDL_WINDOWEVENT      = 0x200
SDL_KEYDOWN          = 0x300
SDL_KEYUP            = 0x301
SDL_TEXTINPUT        = 0x303
SDL_MOUSEMOTION      = 0x400
SDL_MOUSEBUTTONDOWN  = 0x401
SDL_MOUSEBUTTONUP    = 0x402


# ── Keysyms ─────────────────────────────────────────────────────────────────
# ASCII printables keep their ord() value (matches PySDL2 SDLK_a..SDLK_z etc.)
# Special keys use values starting at 0x40000000 (PySDL2 SDLK_SCANCODE_MASK
# convention) — we don't need byte-for-byte parity, just unique constants.

_SCANMASK = 0x40000000

SDLK_RETURN     = 13
SDLK_ESCAPE     = 27
SDLK_BACKSPACE  =  8
SDLK_TAB        =  9
SDLK_SPACE      = 32

SDLK_F1  = _SCANMASK | 0x3A
SDLK_F2  = _SCANMASK | 0x3B
SDLK_F3  = _SCANMASK | 0x3C
SDLK_F4  = _SCANMASK | 0x3D
SDLK_F5  = _SCANMASK | 0x3E
SDLK_F6  = _SCANMASK | 0x3F
SDLK_F7  = _SCANMASK | 0x40
SDLK_F8  = _SCANMASK | 0x41
SDLK_F9  = _SCANMASK | 0x42
SDLK_F10 = _SCANMASK | 0x43
SDLK_F11 = _SCANMASK | 0x44
SDLK_F12 = _SCANMASK | 0x45

SDLK_UP    = _SCANMASK | 0x52
SDLK_DOWN  = _SCANMASK | 0x51
SDLK_LEFT  = _SCANMASK | 0x50
SDLK_RIGHT = _SCANMASK | 0x4F

SDLK_LSHIFT = _SCANMASK | 0xE1
SDLK_RSHIFT = _SCANMASK | 0xE5
SDLK_LCTRL  = _SCANMASK | 0xE0
SDLK_RCTRL  = _SCANMASK | 0xE4
SDLK_LALT   = _SCANMASK | 0xE2

# Modifier mask (PySDL2 KMOD_*)
KMOD_NONE   = 0x0000
KMOD_LSHIFT = 0x0001
KMOD_RSHIFT = 0x0002
KMOD_SHIFT  = KMOD_LSHIFT | KMOD_RSHIFT
KMOD_LCTRL  = 0x0040
KMOD_RCTRL  = 0x0080
KMOD_CTRL   = KMOD_LCTRL | KMOD_RCTRL
KMOD_LALT   = 0x0100
KMOD_RALT   = 0x0200
KMOD_ALT    = KMOD_LALT | KMOD_RALT


# Internal: gui.input.KEY_* → SDLK_* mapping.
_KEY_TO_SDLK = {
    _gui_input.KEY_ENTER:      SDLK_RETURN,
    _gui_input.KEY_ESC:        SDLK_ESCAPE,
    _gui_input.KEY_BACKSPACE:  SDLK_BACKSPACE,
    _gui_input.KEY_TAB:        SDLK_TAB,
    _gui_input.KEY_SPACE:      SDLK_SPACE,
    _gui_input.KEY_LSHIFT:     SDLK_LSHIFT,
    _gui_input.KEY_RSHIFT:     SDLK_RSHIFT,
    _gui_input.KEY_LCTRL:      SDLK_LCTRL,
    _gui_input.KEY_LALT:       SDLK_LALT,
    _gui_input.KEY_F1:  SDLK_F1, _gui_input.KEY_F2:  SDLK_F2,
    _gui_input.KEY_F3:  SDLK_F3, _gui_input.KEY_F4:  SDLK_F4,
    _gui_input.KEY_F5:  SDLK_F5, _gui_input.KEY_F6:  SDLK_F6,
    _gui_input.KEY_F7:  SDLK_F7, _gui_input.KEY_F8:  SDLK_F8,
    _gui_input.KEY_F9:  SDLK_F9, _gui_input.KEY_F10: SDLK_F10,
    _gui_input.KEY_F11: SDLK_F11, _gui_input.KEY_F12: SDLK_F12,
    _gui_input.KEY_UP:    SDLK_UP,
    _gui_input.KEY_DOWN:  SDLK_DOWN,
    _gui_input.KEY_LEFT:  SDLK_LEFT,
    _gui_input.KEY_RIGHT: SDLK_RIGHT,
}


def _to_sdl_keysym(code: int) -> int:
    if code in _KEY_TO_SDLK:
        return _KEY_TO_SDLK[code]
    if 0x20 <= code < 0x80:   # printable ASCII
        return code
    return code | _SCANMASK    # opaque pass-through


def _to_sdl_kmod(mods: int) -> int:
    out = 0
    if mods & _gui_input.MOD_SHIFT: out |= KMOD_LSHIFT
    if mods & _gui_input.MOD_CTRL:  out |= KMOD_LCTRL
    if mods & _gui_input.MOD_ALT:   out |= KMOD_LALT
    return out


# ── Event records ───────────────────────────────────────────────────────────

@dataclass
class _SDLKeysym:
    sym:      int = 0
    scancode: int = 0
    mod:      int = 0


@dataclass
class _SDLKeyboardEvent:
    type:      int = 0
    timestamp: int = 0
    state:     int = 0     # 1=pressed, 0=released
    repeat:    int = 0
    keysym:    _SDLKeysym = field(default_factory=_SDLKeysym)


@dataclass
class _SDLMouseMotionEvent:
    type:      int = 0
    timestamp: int = 0
    x:         int = 0
    y:         int = 0
    xrel:      int = 0
    yrel:      int = 0
    state:     int = 0


@dataclass
class _SDLMouseButtonEvent:
    type:      int = 0
    timestamp: int = 0
    button:    int = 0
    state:     int = 0
    x:         int = 0
    y:         int = 0


@dataclass
class _SDLQuitEvent:
    type:      int = 0
    timestamp: int = 0


class SDL_Event:
    """Tagged union — only one of `key`, `motion`, `button`, `quit` is
    meaningful per :func:`SDL_PollEvent` call. The ``type`` field tells
    you which."""

    def __init__(self) -> None:
        self.type = 0
        self.timestamp = 0
        self.key    = _SDLKeyboardEvent()
        self.motion = _SDLMouseMotionEvent()
        self.button = _SDLMouseButtonEvent()
        self.quit   = _SDLQuitEvent()


# ── Polling ─────────────────────────────────────────────────────────────────

def _populate(out: SDL_Event, ev) -> None:
    """Translate kernel.gui.input.Event into the SDL_Event union."""
    if ev.kind in (_gui_input.EVENT_KEY_DOWN, _gui_input.EVENT_KEY_UP):
        out.type = SDL_KEYDOWN if ev.kind == _gui_input.EVENT_KEY_DOWN else SDL_KEYUP
        out.key.type = out.type
        out.key.state = 1 if ev.kind == _gui_input.EVENT_KEY_DOWN else 0
        out.key.repeat = 0
        out.key.keysym.sym = _to_sdl_keysym(ev.code)
        out.key.keysym.scancode = ev.code
        out.key.keysym.mod = _to_sdl_kmod(ev.mods)
    elif ev.kind == _gui_input.MOUSE_MOVE:
        out.type = SDL_MOUSEMOTION
        out.motion.type = SDL_MOUSEMOTION
        out.motion.x, out.motion.y   = ev.x, ev.y
        out.motion.xrel, out.motion.yrel = ev.dx, ev.dy
    elif ev.kind == _gui_input.MOUSE_DOWN:
        out.type = SDL_MOUSEBUTTONDOWN
        out.button.type = SDL_MOUSEBUTTONDOWN
        out.button.button = ev.code
        out.button.state = 1
        out.button.x, out.button.y = ev.x, ev.y
    elif ev.kind == _gui_input.MOUSE_UP:
        out.type = SDL_MOUSEBUTTONUP
        out.button.type = SDL_MOUSEBUTTONUP
        out.button.button = ev.code
        out.button.state = 0
        out.button.x, out.button.y = ev.x, ev.y
    elif ev.kind == _gui_input.QUIT:
        out.type = SDL_QUIT
        out.quit.type = SDL_QUIT


def SDL_PollEvent(event: SDL_Event) -> int:
    """Drain at most one event from the queue. Returns 1 if an event was
    written into ``event``, 0 otherwise."""
    q = _gui_input.queue
    if q == None or q.empty():
        return 0
    ev = q.poll()
    if ev == None:
        return 0
    _populate(event, ev)
    return 1


async def SDL_WaitEvent(event: SDL_Event) -> int:
    q = _gui_input.queue
    if q == None:
        _gui_input.init()
        q = _gui_input.queue
    ev = await q.get()
    _populate(event, ev)
    return 1


def SDL_PumpEvents() -> None:
    """No-op: events arrive into the queue from interrupt context, so
    no pumping is needed (matches SDL semantics where Pump is implicit
    in Poll)."""
    pass

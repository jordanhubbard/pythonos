"""
kernel.gui.input — Canonical input events for the GUI subsystem.

Both PS/2 (x86) and virtio-input (arm64) drivers normalize their hardware
events into :class:`Event` records and post them to the module-level
:data:`queue`. Consumers (compositor, sdl2 shim) ``await queue.get()``
or call ``queue.poll()`` for non-blocking access.

Key codes are not Linux EV_KEY values nor PS/2 scancodes verbatim — they
are a small portable enum (KEY_*) that both drivers translate into. This
keeps the sdl2.events module independent of the specific input backend.
"""

import asyncio
from dataclasses import dataclass


# ── Event kinds ─────────────────────────────────────────────────────────────

# Event kinds deliberately use a distinct namespace from key codes.  Do not
# call these KEY_DOWN/KEY_UP: those names are needed for the arrow keys below.
EVENT_KEY_DOWN = 1
EVENT_KEY_UP   = 2
MOUSE_MOVE   = 3
MOUSE_DOWN   = 4
MOUSE_UP     = 5
MOUSE_WHEEL  = 6
QUIT         = 100


# ── Modifier mask ───────────────────────────────────────────────────────────

MOD_SHIFT = 1 << 0
MOD_CTRL  = 1 << 1
MOD_ALT   = 1 << 2
MOD_META  = 1 << 3
MOD_CAPS  = 1 << 4


# ── Key codes ───────────────────────────────────────────────────────────────
# ASCII letters/digits/punctuation keep their ord() value as the key code so
# `Event.code == ord('a')` works for plain alphanumerics. Non-printable keys
# get codes in 0xA0..0xCF (well clear of the 0..127 ASCII range).

KEY_NONE      = 0
KEY_BACKSPACE = 8
KEY_TAB       = 9
KEY_ENTER     = 13
KEY_ESC       = 27
KEY_SPACE     = 32

KEY_LSHIFT    = 0xA0
KEY_RSHIFT    = 0xA1
KEY_LCTRL     = 0xA2
KEY_RCTRL     = 0xA3
KEY_LALT      = 0xA4
KEY_RALT      = 0xA5
KEY_CAPS_LOCK = 0xA6

KEY_F1, KEY_F2, KEY_F3, KEY_F4   = 0xB1, 0xB2, 0xB3, 0xB4
KEY_F5, KEY_F6, KEY_F7, KEY_F8   = 0xB5, 0xB6, 0xB7, 0xB8
KEY_F9, KEY_F10, KEY_F11, KEY_F12 = 0xB9, 0xBA, 0xBB, 0xBC

KEY_UP        = 0xC0
KEY_DOWN      = 0xC1
KEY_LEFT      = 0xC2
KEY_RIGHT     = 0xC3
KEY_HOME      = 0xC4
KEY_END       = 0xC5
KEY_PAGE_UP   = 0xC6
KEY_PAGE_DOWN = 0xC7
KEY_DELETE    = 0xC8
KEY_INSERT    = 0xC9


# Mapping from the existing PS/2 driver's key-name string to KEY_* codes.
# Only listed for non-printable keys; printable keys use ord(char).
_KEY_NAME_TO_CODE = {
    'esc':       KEY_ESC,
    'tab':       KEY_TAB,
    '\n':        KEY_ENTER,
    'backspace': KEY_BACKSPACE,
    'lshift':    KEY_LSHIFT,
    'rshift':    KEY_RSHIFT,
    'lctrl':     KEY_LCTRL,
    'lalt':      KEY_LALT,
    'capslock':  KEY_CAPS_LOCK,
    'up':        KEY_UP,
    'down':      KEY_DOWN,
    'left':      KEY_LEFT,
    'right':     KEY_RIGHT,
    'delete':    KEY_DELETE,
}


# ── Event ───────────────────────────────────────────────────────────────────

@dataclass
class Event:
    kind: int                   # one of the constants above
    code: int = 0               # KEY_* for keys; mouse button index for buttons
    text: str = ""              # typed character (after shift/caps); "" for non-text
    mods: int = 0               # MOD_* bitmask
    x:    int = 0
    y:    int = 0
    dx:   int = 0
    dy:   int = 0


# ── EventQueue ──────────────────────────────────────────────────────────────

class EventQueue:
    """Asyncio-backed event queue. Drivers call :meth:`post` from any task;
    consumers await :meth:`get` or call :meth:`poll`."""

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    def post(self, ev: Event) -> None:
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    async def get(self) -> Event:
        return await self._q.get()

    def poll(self):
        if self._q.empty():
            return None
        return self._q.get_nowait()

    def empty(self) -> bool:
        return self._q.empty()


queue: EventQueue | None = None


def init() -> EventQueue:
    """Idempotent setup; safe to call multiple times."""
    global queue
    if queue == None:
        queue = EventQueue()
    return queue


# ── PS/2 keyboard bridge (x86) ──────────────────────────────────────────────

def _key_name_to_code(key_name: str, char: str) -> int:
    """Translate the PS/2 driver's key-name to our portable KEY_* code."""
    if key_name in _KEY_NAME_TO_CODE:
        return _KEY_NAME_TO_CODE[key_name]
    if len(key_name) == 1:
        return ord(key_name)
    if char and len(char) == 1:
        return ord(char)
    return KEY_NONE


def install_ps2_bridge() -> None:
    """Subscribe to the PS/2 driver's IRQ callbacks and forward each key
    event into :data:`queue` as a normalized :class:`Event`. Called from
    :func:`kernel.boot` once the framebuffer is up.

    Idempotent — does nothing on a second call.
    """
    init()
    from kernel.drivers.keyboard import keyboard

    if getattr(keyboard, '_gui_bridge_installed', False):
        return
    keyboard._gui_bridge_installed = True

    def _forward(ke) -> None:
        # ke is kernel.drivers.keyboard.KeyEvent
        kind = EVENT_KEY_DOWN if ke.pressed else EVENT_KEY_UP
        mods = 0
        if ke.shift: mods |= MOD_SHIFT
        if ke.ctrl:  mods |= MOD_CTRL
        if ke.alt:   mods |= MOD_ALT
        code = _key_name_to_code(ke.key, ke.char)
        ev = Event(kind=kind, code=code, text=ke.char, mods=mods)
        queue.post(ev)

    keyboard.subscribe(_forward)
    keyboard.init()  # unmask IRQ1


# ── PS/2 mouse bridge (x86) ─────────────────────────────────────────────────

# Canonical PS/2-mouse button-mask → SDL/sdl2 button index mapping.
_MOUSE_BTN_TO_INDEX = {
    0x01: 1,   # PKT_LBTN  → SDL_BUTTON_LEFT
    0x02: 3,   # PKT_RBTN  → SDL_BUTTON_RIGHT
    0x04: 2,   # PKT_MBTN  → SDL_BUTTON_MIDDLE
}


_pointer_x = 512
_pointer_y = 384


def install_ps2_mouse_bridge(width: int = 1024, height: int = 768) -> None:
    """Subscribe to the PS/2 mouse driver's IRQ callbacks and post
    MOUSE_MOVE / MOUSE_DOWN / MOUSE_UP events into :data:`queue`.

    Maintains a kernel-side cumulative pointer position (``_pointer_x``,
    ``_pointer_y``) clamped to ``(width, height)`` so the compositor can
    hit-test windows even though the underlying device only reports
    deltas.
    """
    init()
    global _pointer_x, _pointer_y
    _pointer_x = width // 2
    _pointer_y = height // 2

    from kernel.drivers.mouse import mouse

    if getattr(mouse, '_gui_bridge_installed', False):
        return
    mouse._gui_bridge_installed = True

    def _forward(me) -> None:
        # me is kernel.drivers.mouse.MouseEvent
        global _pointer_x, _pointer_y
        if me.dx != 0 or me.dy != 0:
            _pointer_x = max(0, min(width  - 1, _pointer_x + me.dx))
            _pointer_y = max(0, min(height - 1, _pointer_y + me.dy))
            ev = Event(kind=MOUSE_MOVE, x=_pointer_x, y=_pointer_y,
                       dx=me.dx, dy=me.dy)
            queue.post(ev)
        if me.button_changed:
            kind = MOUSE_DOWN if me.pressed else MOUSE_UP
            code = _MOUSE_BTN_TO_INDEX.get(me.button_changed, 0)
            ev = Event(kind=kind, code=code, x=_pointer_x, y=_pointer_y)
            queue.post(ev)

    mouse.subscribe(_forward)
    mouse.init()  # enable + unmask IRQ12


def pointer_position() -> tuple[int, int]:
    return _pointer_x, _pointer_y

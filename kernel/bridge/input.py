"""
kernel.bridge.input — forwards host SDL events into the guest's
kernel.gui.input EventQueue.

When the bridge is open, this module's `start_forwarder()` schedules
a background task that polls the host bridge ~60 Hz and translates
SDL events into kernel.gui.input.Event records. The compositor's
existing input dispatcher then consumes them — drag, click-to-focus,
keyboard, etc. all work uniformly with what virtio-input used to feed.
"""

import asyncio

import kernel.gui.input as _gui
from kernel.bridge import bridge as _bridge, BridgeError


# SDL event "kind" values (see tools/pythonos_bridge/main.c).
_BR_KEY_DOWN   = 1
_BR_KEY_UP     = 2
_BR_MOUSE_MOVE = 3
_BR_MOUSE_DOWN = 4
_BR_MOUSE_UP   = 5
_BR_QUIT       = 6


# SDL_Keycode → kernel.gui.input KEY_* mapping for non-ASCII keys.
# ASCII keys (sym < 0x80) pass through as their codepoint.
# SDL puts non-ASCII keys at 0x40000000 | scancode.
_SDLK_TO_KERNEL = {
    27:           _gui.KEY_ESC,        # SDLK_ESCAPE
    9:            _gui.KEY_TAB,        # SDLK_TAB
    13:           _gui.KEY_ENTER,      # SDLK_RETURN
    8:            _gui.KEY_BACKSPACE,  # SDLK_BACKSPACE
    32:           _gui.KEY_SPACE,
    0x4000007F:   _gui.KEY_DELETE,
    0x40000050:   _gui.KEY_LEFT,
    0x4000004F:   _gui.KEY_RIGHT,
    0x40000052:   _gui.KEY_UP,
    0x40000051:   _gui.KEY_DOWN,
    0x400000E1:   _gui.KEY_LSHIFT,
    0x400000E5:   _gui.KEY_RSHIFT,
    0x400000E0:   _gui.KEY_LCTRL,
    0x400000E4:   _gui.KEY_RCTRL,
    0x400000E2:   _gui.KEY_LALT,
    0x400000E6:   _gui.KEY_RALT,
    0x40000039:   _gui.KEY_CAPS_LOCK,
    0x4000003A:   _gui.KEY_F1,
    0x4000003B:   _gui.KEY_F2,
    0x4000003C:   _gui.KEY_F3,
    0x4000003D:   _gui.KEY_F4,
    0x4000003E:   _gui.KEY_F5,
    0x4000003F:   _gui.KEY_F6,
    0x40000040:   _gui.KEY_F7,
    0x40000041:   _gui.KEY_F8,
}


def _translate_keycode(sym: int) -> int:
    if sym in _SDLK_TO_KERNEL:
        return _SDLK_TO_KERNEL[sym]
    if sym < 0x80:
        return sym
    return 0  # unknown


def _translate(ev: dict):
    kind = ev.get("kind", 0)
    x    = int(ev.get("x", 0)); y  = int(ev.get("y", 0))
    dx   = int(ev.get("dx", 0)); dy = int(ev.get("dy", 0))
    if kind == _BR_MOUSE_MOVE:
        return _gui.Event(kind=_gui.MOUSE_MOVE, x=x, y=y, dx=dx, dy=dy)
    if kind == _BR_MOUSE_DOWN:
        return _gui.Event(kind=_gui.MOUSE_DOWN, code=int(ev.get("button", 0)),
                          x=x, y=y)
    if kind == _BR_MOUSE_UP:
        return _gui.Event(kind=_gui.MOUSE_UP,   code=int(ev.get("button", 0)),
                          x=x, y=y)
    if kind == _BR_KEY_DOWN:
        return _gui.Event(kind=_gui.EVENT_KEY_DOWN,
                          code=_translate_keycode(int(ev.get("code", 0))),
                          text=ev.get("text", ""))
    if kind == _BR_KEY_UP:
        return _gui.Event(kind=_gui.EVENT_KEY_UP,
                          code=_translate_keycode(int(ev.get("code", 0))))
    if kind == _BR_QUIT:
        return _gui.Event(kind=_gui.QUIT)
    return None


_running = False


async def _pump() -> None:
    """Poll the bridge for events at ~60 Hz; post each to the GUI queue."""
    global _running
    if _gui.queue is None:
        _gui.init()
    while _running:
        try:
            r = _bridge.call("event.poll", {})
        except BridgeError:
            await asyncio.sleep(0.5)
            continue
        for raw in r.get("events", []):
            ev = _translate(raw)
            if ev is not None:
                _gui.queue.post(ev)
        await asyncio.sleep(1.0 / 60)


def start_forwarder(loop=None) -> None:
    """Start the bridge-input pump as a background task. Idempotent."""
    global _running
    if _running:
        return
    _running = True
    loop = loop or asyncio.get_event_loop()
    loop.create_task(_pump())


def stop_forwarder() -> None:
    global _running
    _running = False

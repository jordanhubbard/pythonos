"""kernel.gui.sdl2.dispatch — generic libSDL2 call dispatcher.

The mirror-SDL bridge protocol (see bd memory key 'gui-architecture-mirror-sdl')
ships only one op for graphics: ``sdl.call(name, args)``. The host registers
each libSDL2 function in a small table; adding a new SDL function on the
guest side is a one-line wrapper here.

The two helpers in this module are the only thing every wrapper needs:

    sdl_call(name, *args) -> dict
        Synchronous round-trip. Returns the host's result dict (typically
        {"rc": <int>} for status-returning SDL functions). Use when the
        caller needs the return value.

    sdl_cast(name, *args) -> None
        Fire-and-forget. The op is queued in the bridge's pending list and
        sent in a single batched round-trip on the next sync call (or on
        ``bridge.flush()``). Use for animation draws where the return code
        is uninteresting.
"""

from kernel.bridge import bridge as _bridge


def sdl_call(name: str, *args) -> dict:
    """Run an SDL function on the host synchronously, return its result."""
    return _bridge.call("sdl.call", {"name": name, "args": list(args)})


def sdl_cast(name: str, *args) -> None:
    """Queue an SDL function for fire-and-forget batch dispatch."""
    _bridge.cast("sdl.call", {"name": name, "args": list(args)})

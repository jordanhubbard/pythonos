"""
kernel.bridge — guest-side client for the pythonos_bridge host
companion.

Calls are SYNCHRONOUS: the bulk-read C primitive (kernel.hal.io
pl011_read_buf / uart16550_read_buf) blocks the kernel scheduler for
the duration of each call. Acceptable because bridge responses are
small (typically under 200 bytes) and the round-trip is dominated by
QEMU's MMIO trap cost, which an async-yield wouldn't help. Apps call
`bridge.call(...)` from any context — no `await` required.

Wire format (mirrors NanoVM/pybridge):
  4-byte big-endian length, then UTF-8 JSON payload.

Frame schemas:
    request:  {"v":1, "id":<int>, "op":<str>, "params":{...}}
    response: {"v":1, "id":<int>, "ok":true,  "result":{...}}
    error:    {"v":1, "id":<int>, "ok":false, "error":{"code":..., "msg":...}}

When `params` carries `payload_len: N`, exactly N raw bytes follow the
JSON envelope (binary trailer). The bridge uses this for one-shot
pixel uploads (surface.upload).
"""

import struct

from kernel.bridge import uart as _uart
import kernel.log as log


# `json` is not in every frozen-Python build (notably arm64). Defer it
# behind a function so that simply importing kernel.bridge — which
# happens transitively whenever anything pulls in kernel.gui.sdl2 —
# doesn't crash the boot. Bridge calls themselves naturally fail if
# json is unavailable on the running build.
def _json():
    import json
    return json


PROTOCOL_VERSION = 1


class BridgeError(Exception):
    """Raised when the host returns ``ok:false``."""
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"bridge error {code}: {msg}")
        self.code = code
        self.msg  = msg


class Bridge:
    """Singleton client. Calls are synchronous; the kernel scheduler is
    blocked for the duration of each call (typically tens of µs).

    Two send modes:
      ``call(op, ...)``    — synchronous round-trip; returns result dict.
      ``cast(op, params)`` — fire-and-forget; the op is queued. Sent in
                              one batch on the next ``flush()`` or before
                              the next ``call()``. Use for ops whose
                              return value the caller doesn't need
                              (FillRect, Blit, text.draw): turns N
                              round-trips per frame into 1.
    """

    def __init__(self) -> None:
        self._next_id = 1
        self._opened  = False
        # Queue of fire-and-forget ops accumulated by cast(). A subsequent
        # call() or explicit flush() drains it as one batch.
        self._pending: list = []

    def hello(self, timeout_ms: int | None = 2000) -> dict:
        """Handshake. Returns the host's hello result."""
        r = self.call("hello", {"protocol": PROTOCOL_VERSION},
                      timeout_ms=timeout_ms)
        self._opened = True
        return r

    @property
    def opened(self) -> bool:
        return self._opened

    def cast(self, op: str, params: dict | None = None) -> None:
        """Queue a fire-and-forget op. Sent in one batch on the next
        flush() or before the next sync call(). Must NOT be used for
        ops that return data the caller needs (surface.create, hello,
        event.poll, surface.upload)."""
        self._pending.append({"op": op, "params": dict(params or {})})

    def flush(self) -> None:
        """Send any queued cast() ops as a single batch round-trip."""
        if not self._pending:
            return
        ops = self._pending
        self._pending = []
        self._send("batch", {"ops": ops}, b"")

    def call(self, op: str, params: dict | None = None,
              payload: bytes = b"",
              timeout_ms: int | None = None) -> dict:
        """Send `op` with `params` (and optional binary `payload`),
        block on the response, return the result dict on success or
        raise BridgeError on failure. Auto-flushes any pending casts
        first to preserve order between cast and call sequences."""
        if self._pending:
            ops = self._pending
            self._pending = []
            self._send("batch", {"ops": ops}, b"", timeout_ms=timeout_ms)
        return self._send(op, params, payload, timeout_ms=timeout_ms)

    def _send(self, op: str, params: dict | None,
               payload: bytes,
               timeout_ms: int | None = None) -> dict:
        json = _json()
        frame_id = self._next_id
        self._next_id += 1
        env_params = dict(params or {})
        if payload:
            env_params["payload_len"] = len(payload)
        body = json.dumps({
            "v": PROTOCOL_VERSION, "id": frame_id,
            "op": op, "params": env_params,
        }).encode("utf-8")

        _uart.write_bytes(struct.pack(">I", len(body)) + body)
        if payload:
            _uart.write_bytes(payload)

        if timeout_ms is None:
            hdr = _uart.read_bytes(4)
        else:
            hdr = _uart.read_bytes_timeout(4, timeout_ms)
            if hdr is None:
                raise BridgeError(-4, "timeout waiting for bridge response")
        (length,) = struct.unpack(">I", hdr)
        if length == 0 or length > 16 * 1024 * 1024:
            raise BridgeError(-1, f"absurd response length {length}")
        if timeout_ms is None:
            body = _uart.read_bytes(length)
        else:
            body = _uart.read_bytes_timeout(length, timeout_ms)
            if body is None:
                raise BridgeError(-4, "timeout waiting for bridge response body")
        env = json.loads(body.decode("utf-8"))

        if env.get("id") != frame_id:
            raise BridgeError(-2, f"id mismatch (sent {frame_id}, got {env.get('id')})")
        if not env.get("ok"):
            err = env.get("error") or {}
            raise BridgeError(err.get("code", -3), err.get("msg", "unknown error"))
        return env.get("result") or {}


# Module-level singleton — apps call kernel.bridge.bridge.call(...)
bridge = Bridge()


def open_bridge() -> bool:
    """Probe the host bridge with a hello. Returns True on success.
    Logs a clear diagnostic and returns False on failure."""
    try:
        r = bridge.hello()
    except Exception as e:
        log.warn(f"bridge: hello failed ({e})")
        return False
    log.info(f"bridge: ready, agent={r.get('agent')} sdl={r.get('sdl_ver')}")
    return True


def _seed_system_menus(compositor, registry) -> None:
    """Build the desktop's system menu bar from the app registry.

    Three top-level menus are always present:
        PythonOS — about / version info.
        Apps     — clickable launcher for every category="app" entry.
        Demos    — clickable launcher for every category="demo" entry.

    Each menu item's action launches the corresponding app via the same
    code path the dock uses (``compositor.launch_app(name)``), so menu
    launches and dock launches behave identically (window creation,
    app_name stamping, app-menu pickup on focus).
    """
    from kernel.gui.menubar import Menu, MenuItem

    def _about() -> None:
        # Launch the About window via the standard launch path so the
        # window is z-stacked + tracked like any other app.
        if registry.get("about") is not None:
            compositor.launch_app("about")
        else:
            log.info("PythonOS — Python is the kernel.")

    def _launcher(app_name):
        # Bind app_name into a closure so each menu item dispatches
        # to its own app rather than capturing the loop variable.
        return lambda: compositor.launch_app(app_name)

    apps_menu_items = [
        MenuItem(info.description or info.name, action=_launcher(info.name))
        for info in sorted(
            (a for a in registry.list_apps() if a.category == "app"),
            key=lambda a: a.name,
        )
    ]
    demos_menu_items = [
        MenuItem(info.description or info.name, action=_launcher(info.name))
        for info in sorted(
            (a for a in registry.list_apps() if a.category == "demo"),
            key=lambda a: a.name,
        )
    ]
    system_menus = [
        Menu("PythonOS", [
            MenuItem("About PythonOS", action=_about),
            MenuItem.sep(),
            MenuItem("Version: 3.14.0a0", enabled=False),
        ]),
        Menu("Apps",  apps_menu_items  or [MenuItem("(none)", enabled=False)]),
        Menu("Demos", demos_menu_items or [MenuItem("(none)", enabled=False)]),
    ]
    compositor._menubar.set_system_menus(system_menus)


def py_desktop(app_name: str | None = None):
    """Open the PythonOS desktop on the host pythonos_bridge. Returns
    the compositor instance on success or ``None`` if no bridge is
    reachable.

    Callable from any REPL session — same behaviour for the kernel
    shell, the TCP REPL, or a bare invocation. Idempotent: subsequent
    calls just return the live compositor without re-launching."""
    try:
        bridge.hello()
    except Exception as e:
        log.warn(f"py_desktop: bridge unavailable ({e})")
        return None
    from kernel.gui.compositor import compositor
    # Pull in all apps so their registry.register() calls fire.
    try:
        import apps                  # noqa: F401
        import apps.demos             # noqa: F401
        import apps.terminal          # noqa: F401
        import apps.editor            # noqa: F401
        import apps.image_viewer      # noqa: F401
        import apps.files             # noqa: F401
        import apps.sysmon            # noqa: F401
        import apps.about             # noqa: F401
        import apps.clock             # noqa: F401
        import apps.toaster           # noqa: F401
        from apps import registry
        # Dock holds full apps; demos live only in the menu bar so the
        # dock stays a launcher for everyday tools.
        for info in registry.list_apps():
            if info.category != "app":
                continue
            compositor.register_dock_app(info.name, info.entry,
                                          info.icon_factory)
        _seed_system_menus(compositor, registry)
    except Exception as e:
        log.warn(f"py_desktop: app registration: {e}")
    try:
        from kernel.chipset import start_for_gui
        start_for_gui()
    except Exception as e:
        log.warn(f"py_desktop: chipset start skipped: {e}")
    compositor.start()
    if app_name:
        try:
            compositor.launch_app(str(app_name))
        except Exception as e:
            log.warn(f"py_desktop: launch {app_name!r} failed: {e}")
    return compositor

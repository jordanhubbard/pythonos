"""
kernel.bridge — guest-side client for the pythonos_bridge host
companion. Apps call ``await bridge.call(op, params)`` to invoke a
host-side operation; the wire format mirrors NanoVM/pybridge:

  4-byte big-endian length, then UTF-8 JSON payload:
    request:  {"v":1, "id":<int>, "op":<str>, "params":{...}}
    response: {"v":1, "id":<int>, "ok":true,  "result":{...}}
    error:    {"v":1, "id":<int>, "ok":false, "error":{"code":..., "msg":...}}

Slice 2 ships the transport + handshake (hello / ping / shutdown).
Display / audio / input ops land in Slice 3.
"""

import asyncio
import json
import struct

from kernel.bridge import uart as _uart
import kernel.log as log


PROTOCOL_VERSION = 1


class BridgeError(Exception):
    """Raised when the host returns ``ok:false``."""
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"bridge error {code}: {msg}")
        self.code = code
        self.msg  = msg


class Bridge:
    """Singleton client. Serializes calls so concurrent coroutines
    can't interleave frames on the byte stream."""

    def __init__(self) -> None:
        self._next_id = 1
        self._lock    = asyncio.Lock()
        self._opened  = False

    async def hello(self) -> dict:
        """Handshake. Returns the host's hello result (agent name,
        protocol version, sdl version)."""
        r = await self.call("hello", {"protocol": PROTOCOL_VERSION})
        self._opened = True
        return r

    async def call(self, op: str, params: dict | None = None) -> dict:
        """Send `op` with `params`, await the response, return the
        result dict on success or raise BridgeError on failure."""
        async with self._lock:
            return await self._call_unlocked(op, params or {})

    async def _call_unlocked(self, op: str, params: dict) -> dict:
        frame_id = self._next_id
        self._next_id += 1
        body = json.dumps({
            "v": PROTOCOL_VERSION, "id": frame_id, "op": op, "params": params,
        }).encode("utf-8")

        _uart.write_bytes(struct.pack(">I", len(body)) + body)

        hdr = await _uart.read_bytes(4)
        (length,) = struct.unpack(">I", hdr)
        if length == 0 or length > 16 * 1024 * 1024:
            raise BridgeError(-1, f"absurd response length {length}")
        payload = await _uart.read_bytes(length)
        env = json.loads(payload.decode("utf-8"))

        if env.get("id") != frame_id:
            raise BridgeError(-2, f"id mismatch (sent {frame_id}, got {env.get('id')})")
        if not env.get("ok"):
            err = env.get("error") or {}
            raise BridgeError(err.get("code", -3), err.get("msg", "unknown error"))
        return env.get("result") or {}


# Module-level singleton — apps call kernel.bridge.bridge.call(...)
bridge = Bridge()


async def open_bridge() -> bool:
    """Probe the host bridge with a hello. Returns True on success.
    Logs a clear diagnostic and returns False on timeout."""
    try:
        r = await asyncio.wait_for(bridge.hello(), timeout=2.0)
    except (TimeoutError, BridgeError) as e:
        log.warn(f"bridge: hello failed ({e})")
        return False
    log.info(f"bridge: ready, agent={r.get('agent')} sdl={r.get('sdl_ver')}")
    return True

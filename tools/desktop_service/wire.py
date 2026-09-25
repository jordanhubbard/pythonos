"""Length-prefixed JSON, with bounded frames and explicit protocol versions."""

import asyncio
import json
import struct

MAX_FRAME = 65536


async def read(reader, limit=MAX_FRAME):
    size, = struct.unpack("!I", await reader.readexactly(4))
    if not 0 < size <= limit:
        raise ValueError("frame exceeds limit")
    value = json.loads(await reader.readexactly(size))
    if not isinstance(value, dict):
        raise ValueError("envelope must be an object")
    return value


async def write(writer, value, payload=b""):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    writer.write(struct.pack("!I", len(data)) + data + payload)
    await asyncio.wait_for(writer.drain(), 5)


class RemoteOS:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.lock = asyncio.Lock()
        self.serial = 0

    async def call(self, op, payload=b"", **params):
        async with self.lock:
            self.serial += 1
            if payload:
                params["payload_len"] = len(payload)
            await write(self.writer, dict(v=2, id=self.serial, op=op, params=params), payload)
            reply = await asyncio.wait_for(read(self.reader, 16 * 1024 * 1024), 5)
            if reply.get("v") != 2 or reply.get("id") != self.serial:
                raise RuntimeError("RemoteOS request failed: " + repr(reply))
            if not reply.get("ok"):
                raise ValueError("RemoteOS rejected operation: " + str(reply.get("error")))
            return reply.get("result") or {}

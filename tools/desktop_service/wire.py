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


async def write(writer, value):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    writer.write(struct.pack("!I", len(data)) + data)
    await asyncio.wait_for(writer.drain(), 5)


class RemoteOS:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.lock = asyncio.Lock()
        self.serial = 0

    async def call(self, op, **params):
        async with self.lock:
            self.serial += 1
            await write(self.writer, dict(v=2, id=self.serial, op=op, params=params))
            reply = await asyncio.wait_for(read(self.reader, 16 * 1024 * 1024), 5)
            if reply.get("v") != 2 or reply.get("id") != self.serial or not reply.get("ok"):
                raise RuntimeError("RemoteOS request failed: " + repr(reply))
            return reply.get("result") or {}

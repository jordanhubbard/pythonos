"""Path: /examples/networking/message_desktop.py

Send retained text, a button, and a live plot to the host desktop service.
The client owns the simulated signal and pause state; the host owns all
layout, rendering, focus, and interaction. No SDL or pixel API is used here.
"""

import asyncio
import json
import math
import struct


class Client:
    """One outstanding request at a time over a send/recv byte stream."""

    def __init__(self, connection):
        self.connection = connection
        self.serial = 0
        self.revision = 0

    async def read_exactly(self, size):
        data = bytearray()
        while len(data) < size:
            chunk = await self.connection.recv(size - len(data))
            if not chunk:
                raise OSError("desktop disconnected")
            data.extend(chunk)
        return bytes(data)

    async def call(self, op, **params):
        self.serial += 1
        payload = json.dumps(dict(v=1, id=self.serial, op=op, params=params)).encode()
        if len(payload) > 65536:
            raise ValueError("desktop request exceeds frame limit")
        await self.connection.send(struct.pack("!I", len(payload)) + payload)
        size, = struct.unpack("!I", await self.read_exactly(4))
        if not 0 < size <= 65536:
            raise ValueError("invalid desktop response length")
        response = json.loads(await self.read_exactly(size))
        if response.get("v") != 1 or response.get("id") != self.serial:
            raise ValueError("invalid desktop response")
        if not response.get("ok"):
            raise ValueError(response.get("error", "desktop request failed"))
        return response["result"]

    async def commit(self, commands):
        result = await self.call("commit", revision=self.revision + 1, commands=commands)
        self.revision = result["revision"]


async def demo(connection, frames=None):
    client = Client(connection)
    await client.call("hello")
    await client.commit([
        dict(op="create_view", view="signal", title="Live signal / message-driven application"),
        dict(op="create_node", view="signal", id="heading", kind="text", text="Application data. Host-owned presentation."),
        dict(op="create_node", view="signal", id="status", kind="text", text="Running"),
        dict(op="create_node", view="signal", id="pause", kind="button", text="Pause"),
        dict(op="create_node", view="signal", id="wave", kind="plot", text="Simulated signal  /  0 - 100", min=0, max=100),
    ])
    paused, tick, iteration = False, 0, 0
    while frames is None or iteration < frames:
        events = (await client.call("poll"))["events"]
        commands = []
        for event in events:
            if event["event"] == "closed":
                return
            if event["event"] == "activate" and event["target"] == "pause":
                paused = not paused
                commands.append(dict(op="set_text", view="signal", id="pause", text="Resume" if paused else "Pause"))
                commands.append(dict(op="set_text", view="signal", id="status", text="Paused" if paused else "Running"))
        if not paused:
            value = 50 + 30 * math.sin(tick * 0.14) + 12 * math.sin(tick * 0.037)
            commands.append(dict(op="append_data", view="signal", id="wave", values=[value]))
            tick += 1
        if commands:
            await client.commit(commands)
        iteration += 1
        await asyncio.sleep(0.05)


async def main(argv=None, cwd="/", read_char=None, write=None):
    from kernel.net.tcp import tcp
    argv = argv or []
    connection = await tcp.connect(argv[0] if argv else "10.0.2.2",
                                   int(argv[1]) if len(argv) > 1 else 17020)
    try:
        await demo(connection)
    finally:
        connection.close()
        tcp.remove_connection(connection)

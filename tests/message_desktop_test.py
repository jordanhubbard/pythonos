#!/usr/bin/env python3
"""Protocol/lifecycle tests; --sdl also exercises the real host renderer."""

import asyncio
import copy
import json
import math
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from desktop_service.model import Desktop, Session, LIMITS
from desktop_service.server import Service
from desktop_service.wire import RemoteOS, read, write
from message_desktop import run_demo

SDL = "--sdl" in sys.argv
if SDL:
    sys.argv.remove("--sdl")


def content():
    return [dict(op="create_view", view="v", title="Window"),
            dict(op="create_node", view="v", id="button", kind="button", text="Pause"),
            dict(op="create_node", view="v", id="plot", kind="plot", text="Signal")]


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


class ModelTests(unittest.TestCase):
    def test_failed_commit_is_atomic_and_retryable(self):
        s = Session()
        s.commit(1, content())
        original = copy.deepcopy(s.views)
        with self.assertRaises(ValueError):
            s.commit(2, [dict(op="set_text", view="v", id="button", text="Changed"),
                         dict(op="append_data", view="v", id="plot", values=[float("nan")])])
        self.assertEqual(s.views, original)
        self.assertEqual(s.revision, 1)
        s.commit(2, [dict(op="set_text", view="v", id="button", text="Resume")])
        with self.assertRaises(ValueError):
            s.commit(2, [dict(op="destroy_view", view="v")])

    def test_plot_history_and_layout_are_bounded(self):
        s = Session()
        s.commit(1, content())
        for revision in range(2, 6):
            s.commit(revision, [dict(op="append_data", view="v", id="plot", values=list(range(200)))])
        self.assertEqual(len(s.views["v"]["nodes"]["plot"]["values"]), LIMITS["samples"])
        original = copy.deepcopy(s.views)
        with self.assertRaises(ValueError):
            s.commit(6, [dict(op="create_node", view="v", id=str(i), kind="button") for i in range(10)])
        self.assertEqual(s.views, original)

    def test_host_interaction_and_disconnect_cleanup(self):
        d, s = Desktop(), Session()
        s.commit(1, content())
        d.sessions.append(s)
        d.sync()
        d.input(dict(kind=4, button=1, x=110, y=135))
        d.input(dict(kind=5, button=1, x=110, y=135))
        self.assertEqual(s.events[0], dict(event="activate", view="v", target="button", revision=1))
        d.input(dict(kind=4, button=1, x=100, y=90))
        d.input(dict(kind=3, x=300, y=190))
        d.input(dict(kind=5, button=1, x=300, y=190))
        self.assertEqual((s.views["v"]["x"], s.views["v"]["y"]), (280, 176))
        d.input(dict(kind=1, code=9))
        d.input(dict(kind=1, code=13))
        self.assertEqual(len(s.events), 2)
        d.sessions.remove(s)
        d.sync()
        self.assertEqual(d.stack, [])
        self.assertIsNone(d.focus)

    def test_event_overflow_is_explicit(self):
        s = Session()
        for _ in range(LIMITS["events"] + 1):
            s.emit(dict(event="activate", view="v", target="button"))
        self.assertTrue(s.overflow)
        self.assertEqual(len(s.events), LIMITS["events"])


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Service(None)
        self.server = await asyncio.start_server(self.service.client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.writers = []

    async def asyncTearDown(self):
        self.server.close()
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        await self.service.close()
        await self.server.wait_closed()

    async def connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        return reader, writer

    async def request(self, connection, op, **params):
        reader, writer = connection
        await write(writer, dict(v=1, id=1, op=op, params=params))
        return await asyncio.wait_for(read(reader), 2)

    async def test_negotiation_fragmentation_and_session_isolation(self):
        a, b = await self.connect(), await self.connect()
        self.assertFalse((await self.request(a, "commit", revision=1, commands=content()))["ok"])
        # Split the header and body to exercise stream framing.
        raw = json.dumps(dict(v=1, id=2, op="hello")).encode()
        frame = struct.pack("!I", len(raw)) + raw
        for chunk in (frame[:2], frame[2:8], frame[8:]):
            a[1].write(chunk)
            await a[1].drain()
        self.assertTrue((await read(a[0]))["ok"])
        await self.request(b, "hello")
        self.assertTrue((await self.request(a, "commit", revision=1, commands=content()))["ok"])
        self.assertFalse((await self.request(b, "commit", revision=1,
                                            commands=[dict(op="destroy_view", view="v")]))["ok"])
        self.assertTrue((await self.request(b, "commit", revision=1, commands=content()))["ok"])
        a[1].close()
        await eventually(lambda: len(self.service.desktop.stack) == 1)
        self.assertEqual(self.service.desktop.stack[0][0].revision, 1)
        self.assertEqual((await self.request(b, "poll"))["result"]["events"], [])

    async def test_oversize_and_partial_disconnect_do_not_poison_service(self):
        a = await self.connect()
        a[1].write(struct.pack("!I", 65537))
        await a[1].drain()
        self.assertEqual(await asyncio.wait_for(a[0].read(), 2), b"")
        b = await self.connect()
        b[1].write(b"\x00\x00")
        b[1].close()
        c = await self.connect()
        self.assertTrue((await self.request(c, "hello"))["ok"])


@unittest.skipUnless(SDL, "pass --sdl to exercise RemoteOS-SDL")
class SDLTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_render_actions_and_lifecycle(self):
        accepted = asyncio.get_running_loop().create_future()
        listener = await asyncio.start_server(lambda r, w: accepted.set_result((r, w)), "127.0.0.1", 0)
        process = await asyncio.create_subprocess_exec(
            str(ROOT / "services/remoteos-sdl/remoteos-sdl"), "--connect-tcp",
            "127.0.0.1:" + str(listener.sockets[0].getsockname()[1]),
            env=dict(os.environ, REMOTEOS_SDL_MODE="headless"))
        service = endpoint = backend = None
        tasks = []
        try:
            backend = RemoteOS(*(await asyncio.wait_for(accepted, 10)))
            service = Service(backend)
            await service.renderer.open()
            endpoint = await asyncio.start_server(service.client, "127.0.0.1", 0)
            tasks.append(asyncio.create_task(service.run(open_display=False)))
            tasks.append(asyncio.create_task(run_demo("127.0.0.1", endpoint.sockets[0].getsockname()[1])))
            await eventually(lambda: service.desktop.stack and len(service.desktop.stack[0][0].views["signal"]["nodes"]["wave"]["values"]) >= 20)
            session, vid = service.desktop.stack[0]
            # Real input travels through SDL -> service -> semantic action -> app.
            await backend.call("debug.event.inject", kind=1, code=9)
            await backend.call("debug.event.inject", kind=1, code=13)
            await eventually(lambda: session.views[vid]["nodes"]["status"]["text"] == "Paused")
            count = len(session.views[vid]["nodes"]["wave"]["values"])
            await asyncio.sleep(0.15)
            self.assertEqual(len(session.views[vid]["nodes"]["wave"]["values"]), count)
            # Stop the app's request loop; the host must still handle dragging.
            tasks[1].cancel()
            # Cancellation closes its session, so use an idle second client instead.
            await asyncio.gather(tasks.pop(), return_exceptions=True)
            await eventually(lambda: not service.desktop.stack)
            reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.sockets[0].getsockname()[1])
            await write(writer, dict(v=1, id=1, op="hello"))
            await read(reader)
            commands = content() + [dict(op="append_data", view="v", id="plot", values=[50 + 35 * math.sin(i / 8) for i in range(100)])]
            await write(writer, dict(v=1, id=2, op="commit", params=dict(revision=1, commands=commands)))
            await read(reader)
            idle, _ = service.desktop.stack[0]
            for params in (dict(kind=4, button=1, x=100, y=90), dict(kind=3, x=220, y=150), dict(kind=5, button=1, x=220, y=150)):
                await backend.call("debug.event.inject", **params)
            await eventually(lambda: idle.views["v"]["x"] == 200)
            await asyncio.sleep(0.1)
            capture = Path(os.environ.get("MESSAGE_DESKTOP_CAPTURE", "/tmp/message-desktop.bmp"))
            await backend.call("debug.capture", path=str(capture))
            self.assertGreater(capture.stat().st_size, 1000)
            self.assertGreaterEqual(count, 20)
            # Close chrome removes host resources and delivers exactly one event.
            await backend.call("debug.event.inject", kind=4, button=1, x=820, y=148)
            await eventually(lambda: not idle.views)
            await write(writer, dict(v=1, id=3, op="poll"))
            self.assertEqual((await read(reader))["result"]["events"], [dict(event="closed", view="v", revision=1)])
            writer.close()
            await writer.wait_closed()
        finally:
            if endpoint:
                endpoint.close()
            if service:
                await service.close()
            if endpoint:
                await endpoint.wait_closed()
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if backend:
                backend.writer.close()
                await backend.writer.wait_closed()
            listener.close()
            await listener.wait_closed()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
            for result in results:
                if isinstance(result, Exception):
                    raise result


if __name__ == "__main__":
    unittest.main()

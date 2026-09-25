"""Multi-session desktop endpoint; input and drawing run independently of clients."""

import asyncio

from .model import Desktop, Session, LIMITS, require
from .wire import read, write, MAX_FRAME
from .render import Renderer


class Service:
    def __init__(self, backend):
        self.desktop = Desktop()
        self.renderer = Renderer(backend)
        self.clients = {}
        self.handlers = set()

    async def client(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        session = Session()
        negotiated = False
        try:
            if len(self.clients) >= 8:
                return
            self.clients[session] = writer
            while self.desktop.running:
                request = await asyncio.wait_for(read(reader), 60)
                serial = request.get("id")
                try:
                    require(request.get("v") == 1, "desktop protocol 1 required")
                    require(type(serial) is int and serial > 0, "positive request id required")
                    op, p = request.get("op"), request.get("params", {})
                    require(isinstance(p, dict), "params must be an object")
                    if not negotiated:
                        require(op == "hello", "hello must be first")
                        negotiated = True
                        self.desktop.sessions.append(session)
                        result = dict(protocol=1, limits=dict(LIMITS, frame_bytes=MAX_FRAME),
                                      features=["text", "button", "plot", "atomic_commit"])
                    elif op == "commit":
                        session.commit(p.get("revision"), p.get("commands"))
                        self.desktop.sync()
                        result = dict(revision=session.revision)
                    elif op == "poll":
                        result = dict(events=session.events, revision=session.revision)
                        session.events = []
                    else:
                        raise ValueError("unknown operation")
                    response = dict(v=1, id=serial, ok=True, result=result)
                except ValueError as exc:
                    response = dict(v=1, id=serial, ok=False, error=str(exc))
                await write(writer, response)
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ValueError):
            pass
        finally:
            self.clients.pop(session, None)
            if session in self.desktop.sessions:
                self.desktop.sessions.remove(session)
            self.desktop.sync()
            writer.close()
            self.handlers.discard(task)

    async def run(self, open_display=True):
        if open_display:
            await self.renderer.open()
        while self.desktop.running:
            if self.desktop.dirty:
                result = await self.renderer.draw(self.desktop)
            else:
                result = await self.renderer.backend.call("event.poll")
            for event in result.get("events", []):
                self.desktop.input(event)
            for session, writer in list(self.clients.items()):
                if session.overflow:
                    writer.close()
            await asyncio.sleep(1 / 60)

    async def close(self):
        self.desktop.running = False
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

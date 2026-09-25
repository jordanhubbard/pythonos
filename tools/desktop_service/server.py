"""High-level GUI sessions over TCP; rendering, input, and media stay on the host."""

import asyncio
import uuid

from .model import Desktop, Session, LIMITS, require
from .media import Media, RESOURCE_LIMITS
from .wire import read, write, MAX_FRAME
from .render import Renderer


class Service:
    def __init__(self, backend, automation_dir=None):
        self.desktop = Desktop()
        self.renderer = Renderer(backend)
        self.media = Media(backend)
        self.automation_dir = automation_dir
        self.clients = {}
        self.handlers = set()
        # Commits, input, media lifecycle and rendering share one ordering domain.
        self.lock = asyncio.Lock()

    async def dispatch(self, session, op, p):
        if op == "commit":
            for command in p.get("commands", []) if isinstance(p.get("commands"), list) else []:
                if isinstance(command, dict) and command.get("kind") in ("scene", "video"):
                    feature = "scene3d.render" if command["kind"] == "scene" else "video.playback"
                    require(feature in self.renderer.features, "backend feature unavailable: " + feature)
            actions = session.commit(p.get("revision"), p.get("commands"), session.resources)
            self.desktop.sync()
            for action, vid in actions:
                self.desktop.window_action(session, action, vid)
            return dict(revision=session.revision)
        if op == "poll":
            result = dict(events=session.events, revision=session.revision)
            session.events = []
            return result
        if op == "inspect":
            return self.desktop.describe(session)
        if op == "sync":
            await self.tick()
            if self.desktop.dirty:
                await self.tick()
            return dict(revision=session.revision)
        if op.startswith(("resource.", "media.")):
            if op == "resource.create" and p.get("kind") == "video":
                require("video.playback" in self.renderer.features, "video playback unavailable")
            result = await self.media.request(session, op, p)
            self.desktop.dirty = True
            return result
        if op.startswith("debug."):
            require(self.automation_dir is not None, "automation disabled")
            if op == "debug.input":
                require(set(p) <= {"kind", "x", "y", "button", "code", "mod", "text"}, "invalid input field")
                require(type(p.get("kind")) is int and 1 <= p["kind"] <= 6, "invalid input kind")
                require(all(type(value) is int and -2147483648 <= value <= 2147483647
                            for key, value in p.items() if key != "text"), "invalid input number")
                require(isinstance(p.get("text", ""), str) and len(p.get("text", "")) <= 16, "invalid input text")
                return await self.renderer.backend.call("debug.event.inject", **p)
            if op == "debug.capture":
                require(not p, "capture paths are service-owned")
                await self.tick()
                if self.desktop.dirty:
                    await self.tick()
                name = uuid.uuid4().hex + ".bmp"
                await self.renderer.backend.call("debug.capture", path=str(self.automation_dir / name))
                return dict(file=name)
            if op == "debug.stats":
                return dict(sessions=len(self.desktop.sessions),
                            views=sum(len(s.views) for s in self.desktop.sessions),
                            surfaces=len(self.renderer.surfaces),
                            resources=sum(len(s.resources) for s in self.desktop.sessions),
                            backend=await self.renderer.backend.call("telemetry.snapshot"))
        raise ValueError("unknown operation")

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
                    require(isinstance(op, str) and isinstance(p, dict), "op/params must be string/object")
                    async with self.lock:
                        if not negotiated:
                            require(op == "hello", "hello must be first")
                            negotiated = True
                            self.desktop.sessions.append(session)
                            features = ["text", "button", "plot", "atomic_commit", "window.configure",
                                        "window.stacking", "controls", "inspect", "sync", "audio.wav"]
                            if "scene3d.render" in self.renderer.features:
                                features.append("scene")
                            if "video.playback" in self.renderer.features:
                                features.append("video")
                            result = dict(protocol=1, limits=dict(LIMITS, **RESOURCE_LIMITS, frame_bytes=MAX_FRAME),
                                          features=features)
                        else:
                            result = await self.dispatch(session, op, p)
                    response = dict(v=1, id=serial, ok=True, result=result)
                except ValueError as exc:
                    response = dict(v=1, id=serial, ok=False, error=str(exc))
                await write(writer, response)
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ValueError):
            pass
        finally:
            self.clients.pop(session, None)
            writer.close()
            try:
                async with self.lock:
                    if session in self.desktop.sessions:
                        self.desktop.sessions.remove(session)
                    self.desktop.sync()
                    for rid in list(session.resources):
                        await self.media.release(session, rid)
                    await self.renderer.release_session(session)
            except (ConnectionError, ValueError, RuntimeError, TimeoutError, asyncio.IncompleteReadError):
                # The launcher also tears down all clients if the device service fails.
                pass
            self.handlers.discard(task)

    async def tick(self):
        result = await self.renderer.backend.call("event.poll")
        for event in result.get("events", []):
            self.desktop.input(event)
        await self.media.reconcile(self.desktop.sessions)
        await self.renderer.prepare(self.desktop)
        if self.desktop.dirty:
            result = await self.renderer.draw(self.desktop)
            for event in result.get("events", []):
                self.desktop.input(event)
        for session, writer in list(self.clients.items()):
            if session.overflow:
                writer.close()

    async def run(self, open_display=True):
        if open_display:
            await self.renderer.open()
        while self.desktop.running:
            async with self.lock:
                await self.tick()
            await asyncio.sleep(1 / 60)

    async def close(self):
        self.desktop.running = False
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

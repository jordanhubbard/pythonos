"""Render retained desktop state using the existing RemoteOS device protocol."""

from .model import WIDTH, HEIGHT, layout, CONTROLS


class Renderer:
    def __init__(self, backend):
        self.backend = backend
        self.handle = None
        self.batch_size = 256
        self.surfaces = {}
        self.features = set()

    async def open(self):
        hello = await self.backend.call("hello", protocol=2, client="retained-desktop/1")
        self.features = set(hello["features"])
        self.batch_size = min(256, hello["limits"]["batch_ops"])
        if self.batch_size < 1:
            raise RuntimeError("RemoteOS advertised an invalid batch limit")
        result = await self.backend.call("display.open", w=WIDTH, h=HEIGHT, title="Message Desktop")
        self.handle = result["fb_handle"]

    async def prepare(self, desktop):
        live = set()
        for s in desktop.sessions:
            for vid, view in s.views.items():
                for nid, node, (_, _, w, h) in layout(view):
                    if node["kind"] not in ("scene", "video"):
                        continue
                    key = (s, vid, nid)
                    live.add(key)
                    entry = self.surfaces.get(key)
                    if entry and (entry["w"], entry["h"], entry["kind"]) != (w, h, node["kind"]):
                        await self.backend.call("surface.destroy", handle=entry["handle"])
                        del self.surfaces[key]
                        entry = None
                    if entry is None:
                        result = await self.backend.call("surface.create", w=w, h=h)
                        entry = dict(handle=result["handle"], w=w, h=h, kind=node["kind"], content=None)
                        self.surfaces[key] = entry
                    if node["kind"] == "scene":
                        if entry["content"] != node["vertices"]:
                            await self.backend.call("scene3d.render", handle=entry["handle"], vertices=node["vertices"], clear=0x172230)
                            entry["content"] = node["vertices"]
                            desktop.dirty = True
                    else:
                        r = s.resources[node["resource"]]
                        if entry["content"] != r["handle"]:
                            # The native player keeps a pending decoded frame sized
                            # for its destination. Seeking flushes it before resize
                            # or reattachment to a differently sized view.
                            await self.backend.call("video.seek", handle=r["handle"], seconds=r["seconds"])
                        if r["playing"] or r.get("refresh") or entry["content"] != r["handle"]:
                            try:
                                result = await self.backend.call("video.tick", handle=r["handle"], destination=entry["handle"])
                            except ValueError as exc:
                                # A bad clip must not take down other sessions' UI.
                                await self.backend.call("video.pause", handle=r["handle"])
                                r.update(playing=False, refresh=False, error=str(exc))
                                entry["content"] = r["handle"]
                                s.emit(dict(event="media_error", resource=node["resource"], message=str(exc)))
                                continue
                            if result["eof"] and not r["eof"]:
                                s.emit(dict(event="media_ended", resource=node["resource"]))
                            r.update(result, refresh=False)
                            if r["eof"]:
                                r["playing"] = False
                            entry["content"] = r["handle"]
                            desktop.dirty = True
        for key in list(self.surfaces):
            if key not in live:
                await self.backend.call("surface.destroy", handle=self.surfaces[key]["handle"])
                del self.surfaces[key]

    async def release_session(self, session):
        for key in list(self.surfaces):
            if key[0] is session:
                await self.backend.call("surface.destroy", handle=self.surfaces[key]["handle"])
                del self.surfaces[key]

    async def draw(self, desktop):
        ops = []

        def op(name, **params):
            ops.append(dict(op=name, params=dict(handle=self.handle, **params)))

        def rect(x, y, w, h, rgb):
            op("surface.fill_rect", rect=dict(x=x, y=y, w=w, h=h), rgb=rgb)

        def text(x, y, value, color=0xD9E4F0, columns=74):
            op("text.draw", x=x, y=y, text=value[:columns], fg=color)

        rect(0, 0, WIDTH, HEIGHT, 0x111923)
        rect(0, 0, WIDTH, 44, 0x202F40)
        text(20, 14, "MESSAGE DESKTOP", 0x82DCC8)
        text(710, 14, "Retained views / protocol 1", columns=34)
        text(20, HEIGHT - 28, "Drag a title bar  |  Tab to focus  |  Enter to activate", 0x8094AA)
        for s, vid in desktop.visible_stack:
            v = s.views[vid]
            x, y = v["x"], v["y"]
            rect(x + 6, y + 6, v["w"], v["h"], 0x080D14)
            rect(x, y, v["w"], v["h"], 0x223044)
            rect(x, y, v["w"], 32, 0x36516D if (s, vid) == desktop.visible_stack[-1] else 0x2A3B50)
            text(x + 14, y + 8, v["title"], columns=(v["w"] - 60) // 8)
            text(x + v["w"] - 24, y + 8, "x")
            text(x + v["w"] - 12, y + v["h"] - 12, "/", color=0x8094AA)
            for nid, n, (nx, ny, w, h) in layout(v):
                key = (s, vid, nid)
                if n["kind"] == "text":
                    text(nx, ny + 5, n["text"], columns=w // 8)
                elif n["kind"] in CONTROLS:
                    color = 0x247F79 if key == desktop.pressed else 0x3D657D if key == desktop.hover else 0x304A62
                    rect(nx, ny, w, h, 0x82DCC8 if key == desktop.focus else color)
                    rect(nx + 2, ny + 2, w - 4, h - 4, color)
                    caption = n["text"]
                    if n["kind"] == "checkbox":
                        caption = ("[x] " if n["value"] else "[ ] ") + caption
                    elif n["kind"] == "entry":
                        caption = n["value"] + ("_" if key == desktop.focus else "")
                    elif n["kind"] == "slider":
                        rect(nx + 4, ny + h - 6, int((w - 8) * n["value"] / 100), 3, 0x82DCC8)
                        caption += "  " + str(n["value"])
                    text(nx + 10, ny + 5, caption, color=0xD9E4F0 if n["enabled"] else 0x758398, columns=(w - 20) // 8)
                elif n["kind"] in ("scene", "video"):
                    ops.append(dict(op="surface.blit", params=dict(src=self.surfaces[key]["handle"], dst=self.handle,
                                    dst_rect=dict(x=nx, y=ny, w=w, h=h))))
                elif n["kind"] == "plot":
                    rect(nx, ny, w, h, 0x172230)
                    text(nx + 12, ny + 9, n["text"], 0x91A8BD, columns=(w - 24) // 8)
                    left, top, pw, ph = nx + 14, ny + 36, w - 28, h - 50
                    for j in range(5):
                        gy = top + j * ph // 4
                        op("surface.line", x0=left, y0=gy, x1=left + pw, y1=gy, rgb=0x29394C)
                    values = n["values"]
                    points = [(left + i * pw // max(1, len(values) - 1),
                               top + ph - int(ph * max(0, min(1, (value - n["min"]) / (n["max"] - n["min"])))))
                              for i, value in enumerate(values)]
                    for (x0, y0), (x1, y1) in zip(points, points[1:]):
                        op("surface.line", x0=x0, y0=y0, x1=x1, y1=y1, rgb=0x82DCC8)
        # Clear before awaiting: concurrent commits/input may dirty it again.
        desktop.dirty = False
        for i in range(0, len(ops), self.batch_size):
            result = await self.backend.call("render.batch", ops=ops[i:i + self.batch_size])
            if result.get("errors"):
                raise RuntimeError("RemoteOS rejected drawing operations")
        return await self.backend.call("frame.commit")

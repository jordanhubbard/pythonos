"""Render retained desktop state using the existing RemoteOS device protocol."""

from .model import WIDTH, HEIGHT, layout


class Renderer:
    def __init__(self, backend):
        self.backend = backend
        self.handle = None
        self.batch_size = 256

    async def open(self):
        hello = await self.backend.call("hello", protocol=2, client="retained-desktop/1")
        self.batch_size = min(256, hello["limits"]["batch_ops"])
        if self.batch_size < 1:
            raise RuntimeError("RemoteOS advertised an invalid batch limit")
        result = await self.backend.call("display.open", w=WIDTH, h=HEIGHT, title="Message Desktop")
        self.handle = result["fb_handle"]

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
        for s, vid in desktop.stack:
            v = s.views[vid]
            x, y = v["x"], v["y"]
            rect(x + 6, y + 6, 640, 480, 0x080D14)
            rect(x, y, 640, 480, 0x223044)
            rect(x, y, 640, 32, 0x36516D if (s, vid) == desktop.stack[-1] else 0x2A3B50)
            text(x + 14, y + 8, v["title"], columns=70)
            text(x + 616, y + 8, "x")
            for nid, n, (nx, ny, w, h) in layout(v):
                key = (s, vid, nid)
                if n["kind"] == "text":
                    text(nx, ny + 5, n["text"])
                elif n["kind"] == "button":
                    color = 0x247F79 if key == desktop.pressed else 0x3D657D if key == desktop.hover else 0x304A62
                    rect(nx, ny, w, h, 0x82DCC8 if key == desktop.focus else color)
                    rect(nx + 2, ny + 2, w - 4, h - 4, color)
                    text(nx + 10, ny + 5, n["text"], columns=72)
                else:
                    rect(nx, ny, w, h, 0x172230)
                    text(nx + 12, ny + 9, n["text"], 0x91A8BD, columns=70)
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

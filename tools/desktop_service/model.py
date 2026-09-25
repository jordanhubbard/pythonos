"""Bounded session state, atomic content updates, and host-owned interaction."""

import copy
import math


WIDTH, HEIGHT = 1000, 720
LIMITS = dict(views=4, nodes=24, samples=256, commands=64, events=64)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and 0 < len(value) <= 64, "invalid identifier")
    return value


def label(value):
    require(isinstance(value, str) and len(value) <= 120, "invalid label")
    require(all(32 <= ord(c) <= 126 for c in value), "labels must be printable ASCII")
    return value


class Session:
    def __init__(self):
        self.views = {}
        self.revision = 0
        self.events = []
        self.overflow = False

    def emit(self, event):
        if len(self.events) == LIMITS["events"]:
            self.overflow = True
        else:
            self.events.append(dict(event, revision=self.revision))

    def commit(self, revision, commands):
        require(type(revision) is int and revision == self.revision + 1,
                "revision must be the next session revision")
        require(isinstance(commands, list) and 0 < len(commands) <= LIMITS["commands"],
                "invalid command count")
        views = copy.deepcopy(self.views)
        for c in commands:
            require(isinstance(c, dict), "command must be an object")
            op, vid = c.get("op"), identifier(c.get("view"))
            if op == "create_view":
                require(vid not in views and len(views) < LIMITS["views"], "view exists or limit reached")
                offset = len(views) * 36
                views[vid] = dict(title=label(c.get("title", vid)), x=80 + offset,
                                  y=76 + offset, nodes={})
                continue
            require(vid in views, "unknown view")
            view = views[vid]
            if op == "destroy_view":
                del views[vid]
                continue
            nid = identifier(c.get("id"))
            nodes = view["nodes"]
            if op == "create_node":
                require(nid not in nodes and len(nodes) < LIMITS["nodes"], "node exists or limit reached")
                kind = c.get("kind")
                require(kind in ("text", "button", "plot"), "unsupported node kind")
                require(kind != "plot" or not any(n["kind"] == "plot" for n in nodes.values()),
                        "one plot per view")
                node = dict(kind=kind, text=label(c.get("text", "")))
                if kind == "plot":
                    lo, hi = c.get("min", 0), c.get("max", 100)
                    require(all(type(v) in (int, float) and abs(v) <= 1e9 and math.isfinite(v)
                                for v in (lo, hi)) and lo < hi, "invalid plot range")
                    node.update(min=lo, max=hi, values=[])
                nodes[nid] = node
            else:
                require(nid in nodes, "unknown node")
                node = nodes[nid]
                if op == "set_text":
                    node["text"] = label(c.get("text"))
                elif op == "append_data":
                    values = c.get("values")
                    require(node["kind"] == "plot", "append requires a plot")
                    require(isinstance(values, list) and len(values) <= LIMITS["samples"], "invalid sample count")
                    require(all(type(v) in (int, float) and abs(v) <= 1e9 and math.isfinite(v)
                                for v in values), "samples must be finite numbers within +/-1e9")
                    node["values"] = (node["values"] + values)[-LIMITS["samples"]:]
                elif op == "destroy_node":
                    del nodes[nid]
                else:
                    raise ValueError("unknown command")
            # Layout must fit completely; no invisible interactive controls.
            require(sum(184 if n["kind"] == "plot" else 38 for n in nodes.values()) <= 418,
                    "view content exceeds available height")
        self.views, self.revision = views, revision


def layout(view):
    """The service assigns geometry; applications only supply ordered content."""
    y = view["y"] + 50
    for nid, node in view["nodes"].items():
        h = 172 if node["kind"] == "plot" else 26
        yield nid, node, (view["x"] + 20, y, 600, h)
        y += h + 12


def inside(x, y, rect):
    rx, ry, w, h = rect
    return rx <= x < rx + w and ry <= y < ry + h


class Desktop:
    def __init__(self):
        self.sessions = []
        self.stack = []  # (session, view ID), bottom to top
        self.hover = self.pressed = self.focus = self.drag = None
        self.dirty = True
        self.running = True

    def sync(self):
        self.stack = [(s, v) for s, v in self.stack if s in self.sessions and v in s.views]
        for s in self.sessions:
            for v in s.views:
                if (s, v) not in self.stack:
                    self.stack.append((s, v))
        for name in ("hover", "pressed", "focus", "drag"):
            target = getattr(self, name)
            if target and ((target[0], target[1]) not in self.stack or
                           (name != "drag" and target[2] not in target[0].views[target[1]]["nodes"])):
                setattr(self, name, None)
        self.dirty = True

    def hit(self, x, y):
        for s, vid in reversed(self.stack):
            v = s.views[vid]
            if inside(x, y, (v["x"], v["y"], 640, 480)):
                node = next((nid for nid, n, rect in layout(v)
                             if n["kind"] == "button" and inside(x, y, rect)), None)
                return s, vid, node
        return None

    def activate(self, target):
        s, vid, nid = target
        s.emit(dict(event="activate", view=vid, target=nid))

    def input(self, e):
        kind, x, y = e.get("kind"), e.get("x", 0), e.get("y", 0)
        if kind == 6:
            self.running = False
        elif kind == 3:
            if self.drag:
                s, vid, dx, dy = self.drag
                v = s.views[vid]
                v["x"] = max(0, min(WIDTH - 640, x - dx))
                v["y"] = max(44, min(HEIGHT - 480, y - dy))
                self.dirty = True
            hit = self.hit(x, y)
            hover = hit if hit and hit[2] else None
            if hover != self.hover:
                self.hover = hover
                self.dirty = True
        elif kind == 4 and e.get("button") == 1:
            hit = self.hit(x, y)
            self.pressed = self.focus = None
            if hit:
                s, vid, nid = hit
                self.stack.remove((s, vid))
                self.stack.append((s, vid))
                v = s.views[vid]
                if inside(x, y, (v["x"] + 602, v["y"], 38, 32)):
                    del s.views[vid]
                    s.emit(dict(event="closed", view=vid))
                    self.sync()
                elif y < v["y"] + 32:
                    self.drag = (s, vid, x - v["x"], y - v["y"])
                elif nid:
                    self.pressed = self.focus = hit
            self.dirty = True
        elif kind == 5 and e.get("button") == 1:
            if self.pressed and self.hit(x, y) == self.pressed:
                self.activate(self.pressed)
            self.pressed = self.drag = None
            self.dirty = True
        elif kind == 1 and self.stack:
            code = e.get("code")
            s, vid = self.stack[-1]
            buttons = [(s, vid, nid) for nid, n in s.views[vid]["nodes"].items() if n["kind"] == "button"]
            if code == 9 and buttons:
                idx = buttons.index(self.focus) if self.focus in buttons else -1
                self.focus = buttons[(idx + 1) % len(buttons)]
                self.dirty = True
            elif code in (13, 32) and self.focus in buttons:
                self.activate(self.focus)

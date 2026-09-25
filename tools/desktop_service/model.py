"""Bounded session state, atomic content updates, and host-owned interaction."""

import copy
import math


WIDTH, HEIGHT = 1000, 720
LIMITS = dict(views=4, nodes=24, samples=256, commands=64, events=64)
CONTROLS = ("button", "checkbox", "entry", "slider")
VISUALS = ("plot", "scene", "video")


def number(value, lo=-1e9, hi=1e9):
    return type(value) in (int, float) and lo <= value <= hi and math.isfinite(value)


def scene(vertices):
    require(isinstance(vertices, list) and len(vertices) <= 768 and len(vertices) % 3 == 0,
            "scene requires at most 256 triangles")
    require(all(isinstance(v, list) and len(v) == 7 and
                all(number(n, -1e6, 1e6) for n in v[:4]) and
                all(number(n, 0, 1) for n in v[4:]) for v in vertices), "invalid scene vertex")
    return vertices


def visible(view):
    return view["visible"] and view["state"] != "minimized"


def content_height(view):
    return sum(184 if n["kind"] in VISUALS else 38 for n in view["nodes"].values())


def configure(view, changes):
    require(set(changes) <= {"title", "x", "y", "w", "h", "visible", "state"}, "unknown view property")
    if "title" in changes:
        view["title"] = label(changes["title"])
    if "visible" in changes:
        require(type(changes["visible"]) is bool, "visible must be boolean")
        view["visible"] = changes["visible"]
    state = changes.get("state", view["state"])
    require(state in ("normal", "minimized", "maximized"), "invalid window state")
    if view["state"] == "maximized" and state != "maximized":
        view.update(view.pop("restore"))
    if state == "maximized" and view["state"] != "maximized":
        view["restore"] = {k: view[k] for k in ("x", "y", "w", "h")}
        view.update(x=0, y=44, w=WIDTH, h=HEIGHT - 44)
    view["state"] = state
    geometry = {k: v for k, v in changes.items() if k in ("x", "y", "w", "h")}
    require(not geometry or state != "maximized", "restore before changing maximized geometry")
    view.update(geometry)
    require(all(type(view[k]) is int for k in ("x", "y", "w", "h")), "geometry must be integer")
    require(320 <= view["w"] <= WIDTH and 180 <= view["h"] <= HEIGHT - 44, "invalid window size")
    require(0 <= view["x"] <= WIDTH - view["w"] and 44 <= view["y"] <= HEIGHT - view["h"],
            "window must fit the desktop")


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
        self.resources = {}

    def emit(self, event):
        if len(self.events) == LIMITS["events"]:
            self.overflow = True
        else:
            self.events.append(dict(event, revision=self.revision))

    def commit(self, revision, commands, resources=None):
        require(type(revision) is int and revision == self.revision + 1,
                "revision must be the next session revision")
        require(isinstance(commands, list) and 0 < len(commands) <= LIMITS["commands"],
                "invalid command count")
        views = copy.deepcopy(self.views)
        actions = []
        for c in commands:
            require(isinstance(c, dict), "command must be an object")
            op, vid = c.get("op"), identifier(c.get("view"))
            if op == "create_view":
                require(vid not in views and len(views) < LIMITS["views"], "view exists or limit reached")
                offset = len(views) * 36
                views[vid] = dict(title=label(c.get("title", vid)), x=80 + offset,
                                  y=76 + offset, w=640, h=480, visible=True, state="normal", nodes={})
                changes = c.get("properties", {})
                require(isinstance(changes, dict), "properties must be an object")
                configure(views[vid], changes)
                continue
            require(vid in views, "unknown view")
            view = views[vid]
            if op == "destroy_view":
                del views[vid]
                continue
            if op == "configure_view":
                changes = c.get("properties")
                require(isinstance(changes, dict), "properties must be an object")
                configure(view, changes)
                continue
            if op in ("focus_view", "lower_view"):
                require(visible(view), "cannot focus/lower a hidden or minimized view")
                actions.append((op, vid))
                continue
            nid = identifier(c.get("id"))
            nodes = view["nodes"]
            if op == "create_node":
                require(nid not in nodes and len(nodes) < LIMITS["nodes"], "node exists or limit reached")
                kind = c.get("kind")
                require(kind in ("text", *CONTROLS, *VISUALS), "unsupported node kind")
                require(kind != "plot" or not any(n["kind"] == "plot" for n in nodes.values()),
                        "one plot per view")
                node = dict(kind=kind, text=label(c.get("text", "")))
                if kind in CONTROLS:
                    require(type(c.get("enabled", True)) is bool, "enabled must be boolean")
                    node["enabled"] = c.get("enabled", True)
                if kind == "checkbox":
                    require(type(c.get("value", False)) is bool, "checkbox value must be boolean")
                    node["value"] = c.get("value", False)
                if kind == "entry":
                    node["value"] = label(c.get("value", ""))
                if kind == "slider":
                    require(number(c.get("value", 0), 0, 100), "slider value must be 0..100")
                    node["value"] = c.get("value", 0)
                if kind == "scene":
                    node["vertices"] = scene(c.get("vertices", []))
                if kind == "video":
                    node["resource"] = identifier(c.get("resource"))
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
                elif op == "set_enabled":
                    require(node["kind"] in CONTROLS and type(c.get("enabled")) is bool,
                            "set_enabled requires control and boolean")
                    node["enabled"] = c["enabled"]
                elif op == "set_value":
                    value = c.get("value")
                    kind = node["kind"]
                    require((kind == "checkbox" and type(value) is bool) or
                            (kind == "slider" and number(value, 0, 100)) or
                            (kind == "entry" and isinstance(value, str)), "invalid control value")
                    node["value"] = label(value) if kind == "entry" else value
                elif op == "set_scene":
                    require(node["kind"] == "scene", "set_scene requires a scene node")
                    node["vertices"] = scene(c.get("vertices"))
                else:
                    raise ValueError("unknown command")
            # Layout must fit completely; no invisible interactive controls.
        used_resources = set()
        for view in views.values():
            height = view.get("restore", view)["h"] if view["state"] == "maximized" else view["h"]
            require(content_height(view) <= height - 62, "view content exceeds available height")
            for node in view["nodes"].values():
                if node["kind"] == "video":
                    rid = node["resource"]
                    require(resources is not None and rid in resources and
                            resources[rid].get("ready") and resources[rid]["kind"] == "video",
                            "video requires an owned, ready video resource")
                    require(rid not in used_resources, "a video resource can have only one player node")
                    used_resources.add(rid)
        for op, vid in actions:
            require(vid in views and visible(views[vid]), "window action target no longer visible")
        self.views, self.revision = views, revision
        return actions


def layout(view):
    """The service assigns geometry; applications only supply ordered content."""
    y = view["y"] + 50
    for nid, node in view["nodes"].items():
        h = 172 if node["kind"] in VISUALS else 26
        yield nid, node, (view["x"] + 20, y, view["w"] - 40, h)
        y += h + 12


def inside(x, y, rect):
    rx, ry, w, h = rect
    return rx <= x < rx + w and ry <= y < ry + h


class Desktop:
    def __init__(self):
        self.sessions = []
        self.stack = []  # (session, view ID), bottom to top
        self.hover = self.pressed = self.focus = self.drag = None
        self.resize = None
        self.dirty = True
        self.running = True

    def sync(self):
        self.stack = [(s, v) for s, v in self.stack if s in self.sessions and v in s.views]
        for s in self.sessions:
            for v in s.views:
                if (s, v) not in self.stack:
                    self.stack.append((s, v))
        for name in ("hover", "pressed", "focus", "drag", "resize"):
            target = getattr(self, name)
            if target and ((target[0], target[1]) not in self.stack or
                           not visible(target[0].views[target[1]]) or
                           (name in ("drag", "resize") and target[0].views[target[1]]["state"] != "normal") or
                           (name not in ("drag", "resize") and
                            (target[2] not in target[0].views[target[1]]["nodes"] or
                             not target[0].views[target[1]]["nodes"][target[2]].get("enabled", False)))):
                setattr(self, name, None)
        self.dirty = True

    @property
    def visible_stack(self):
        return [(s, v) for s, v in self.stack if visible(s.views[v])]

    def window_action(self, session, action, vid):
        self.stack.remove((session, vid))
        if action == "focus_view":
            self.stack.append((session, vid))
        else:
            self.stack.insert(0, (session, vid))
        self.focus = self.pressed = None
        self.dirty = True

    def describe(self, session):
        top = self.visible_stack[-1] if self.visible_stack else None
        result = {}
        for vid, view in session.views.items():
            result[vid] = {k: view[k] for k in ("title", "x", "y", "w", "h", "visible", "state")}
            result[vid]["focused"] = top == (session, vid)
            result[vid]["nodes"] = {
                nid: dict({k: val for k, val in node.items() if k != "vertices"}, rect=list(rect),
                          focused=self.focus == (session, vid, nid))
                for nid, node, rect in layout(view)}
        return dict(revision=session.revision, views=result,
                    stacking=[v for s, v in self.stack if s is session])

    def hit(self, x, y):
        for s, vid in reversed(self.visible_stack):
            v = s.views[vid]
            if inside(x, y, (v["x"], v["y"], v["w"], v["h"])):
                node = next((nid for nid, n, rect in layout(v)
                             if n["kind"] in CONTROLS and n["enabled"] and inside(x, y, rect)), None)
                return s, vid, node
        return None

    def activate(self, target):
        s, vid, nid = target
        n = s.views[vid]["nodes"][nid]
        if n["kind"] == "checkbox":
            n["value"] = not n["value"]
            self.changed(target)
        elif n["kind"] == "button":
            s.emit(dict(event="activate", view=vid, target=nid))

    def changed(self, target):
        s, vid, nid = target
        s.emit(dict(event="change", view=vid, target=nid, value=s.views[vid]["nodes"][nid]["value"]))
        self.dirty = True

    def slider(self, target, x):
        s, vid, nid = target
        view = s.views[vid]
        value = round(max(0, min(100, (x - view["x"] - 20) * 100 / (view["w"] - 40))))
        if value != view["nodes"][nid]["value"]:
            view["nodes"][nid]["value"] = value
            self.dirty = True

    def input(self, e):
        kind, x, y = e.get("kind"), e.get("x", 0), e.get("y", 0)
        if kind == 6:
            self.running = False
        elif kind == 3:
            if self.drag:
                s, vid, dx, dy = self.drag
                v = s.views[vid]
                v["x"] = max(0, min(WIDTH - v["w"], x - dx))
                v["y"] = max(44, min(HEIGHT - v["h"], y - dy))
                self.dirty = True
            if self.resize:
                s, vid = self.resize
                v = s.views[vid]
                v["w"] = max(320, min(WIDTH - v["x"], x - v["x"] + 1))
                v["h"] = max(180, content_height(v) + 62, min(HEIGHT - v["y"], y - v["y"] + 1))
                self.dirty = True
            if self.pressed:
                s, vid, nid = self.pressed
                if s.views[vid]["nodes"][nid]["kind"] == "slider":
                    self.slider(self.pressed, x)
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
                if inside(x, y, (v["x"] + v["w"] - 38, v["y"], 38, 32)):
                    del s.views[vid]
                    s.emit(dict(event="closed", view=vid))
                    self.sync()
                elif y < v["y"] + 32 and v["state"] == "normal":
                    self.drag = (s, vid, x - v["x"], y - v["y"])
                elif x >= v["x"] + v["w"] - 14 and y >= v["y"] + v["h"] - 14 and v["state"] == "normal":
                    self.resize = (s, vid)
                elif nid:
                    self.pressed = self.focus = hit
                    if v["nodes"][nid]["kind"] == "slider":
                        self.slider(hit, x)
            self.dirty = True
        elif kind == 5 and e.get("button") == 1:
            target = self.drag or self.resize
            if target:
                s, vid = target[:2]
                v = s.views[vid]
                s.emit(dict(event="configured", view=vid, **{k: v[k] for k in ("x", "y", "w", "h")}))
            if self.pressed:
                s, vid, nid = self.pressed
                if s.views[vid]["nodes"][nid]["kind"] == "slider":
                    self.changed(self.pressed)
            if self.pressed and self.hit(x, y) == self.pressed:
                self.activate(self.pressed)
            self.pressed = self.drag = self.resize = None
            self.dirty = True
        elif kind == 1 and self.visible_stack:
            code = e.get("code")
            s, vid = self.visible_stack[-1]
            buttons = [(s, vid, nid) for nid, n in s.views[vid]["nodes"].items() if n["kind"] in CONTROLS and n["enabled"]]
            if code == 9 and buttons:
                step = -1 if e.get("mod", 0) & 3 else 1
                idx = buttons.index(self.focus) if self.focus in buttons else (0 if step == -1 else -1)
                self.focus = buttons[(idx + step) % len(buttons)]
                self.dirty = True
            elif self.focus in buttons:
                n = s.views[vid]["nodes"][self.focus[2]]
                if n["kind"] == "entry":
                    old = n["value"]
                    if code == 8:
                        n["value"] = old[:-1]
                    else:
                        chars = e.get("text", "")
                        if all(32 <= ord(c) <= 126 for c in chars):
                            n["value"] = (old + chars)[:120]
                    if old != n["value"]:
                        self.changed(self.focus)
                elif n["kind"] == "slider" and code in (0x40000050, 0x4000004F):
                    n["value"] = max(0, min(100, n["value"] + (-1 if code == 0x40000050 else 1)))
                    self.changed(self.focus)
                elif code in (13, 32):
                    self.activate(self.focus)

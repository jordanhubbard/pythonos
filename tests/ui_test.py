#!/usr/bin/env python3
"""Host tests for the beginner-facing GUI view hierarchy."""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if "kernel" not in sys.modules:
    package = types.ModuleType("kernel")
    package.__path__ = [os.path.join(ROOT, "kernel")]
    package.__package__ = "kernel"
    sys.modules["kernel"] = package

from kernel.gui.ui import Button, Container, Label, ListView, TextView, View


class Surface:
    def __init__(self):
        self.ops = []

    def _fill_rect(self, x, y, w, h, color):
        self.ops.append(("fill", x, y, w, h, color))

    def draw_text(self, x, y, text, fg, bg):
        self.ops.append(("text", x, y, text, fg, bg))


class Host:
    def __init__(self):
        self.surface = Surface()
        self.dirty = False


def main() -> int:
    failed = 0

    def check(name, condition):
        nonlocal failed
        print(("  PASS  " if condition else "  FAIL  ") + name)
        failed += not bool(condition)

    print("ui_test")
    host = Host()
    root = Container(0, 0, 100, 60, background=0x10, host=host)
    label = root.add(Label("hello", 4, 5, 40, 10,
                           color=0x20, background=0x10))
    root.draw(host.surface)
    check("containers paint themselves then their children",
          host.surface.ops[0] == ("fill", 0, 0, 100, 60, 0x10)
          and any(op[:4] == ("text", 4, 5, "hello")
                  for op in host.surface.ops))
    label.invalidate()
    check("child invalidation reaches its host window", host.dirty)

    clicked = []
    button = Button("go", 0, 0, 20, 10, action=lambda: clicked.append(1))
    event = types.SimpleNamespace(kind=4, code=1, x=2, y=2)
    check("button consumes clicks and invokes its action",
          button.on_event(event) and clicked == [1])

    listing = ListView(0, 0, 20, 20)
    listing.items = ["a", "b", "c"]
    listing.move_selection(2)
    listing.move_selection(9)
    check("list selection is bounded", listing.selected == 2)
    check("specialized views retain the common base",
          isinstance(listing, TextView) and isinstance(label, View))

    print(f"\n{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Host-side dock + desktop launcher tests. No QEMU, no _hal.

Run: python3 tests/dock_test.py
"""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# kernel/__init__.py is the boot path and imports _hal. Host tests
# register a namespace package so kernel.gui.dock loads without booting.
if "kernel" not in sys.modules:
    _kernel_pkg = types.ModuleType("kernel")
    _kernel_pkg.__path__ = [os.path.join(ROOT, "kernel")]
    _kernel_pkg.__package__ = "kernel"
    sys.modules["kernel"] = _kernel_pkg

_failed = 0
_passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global _failed, _passed
    ok = bool(cond)
    if ok:
        _passed += 1
        print(f"  PASS  {name}" + (f" ({detail})" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class _App:
    def __init__(self, name, category, description=""):
        self.name = name
        self.category = category
        self.description = description or name
        self.entry = lambda: None
        self.icon_factory = None


def main() -> int:
    print("dock_test")
    from kernel.gui.dock import (
        Dock,
        Popup,
        PopupItem,
        desktop_background_hit,
        desktop_popup_items,
        dock_popup_items,
        is_context_click,
        launcher_groups,
        seed_pinned_from_registry,
    )
    from kernel.gui.input import MOD_CTRL

    dock = Dock()
    check("empty dock has no visible icons", dock.visible_names() == [])

    async def _noop():
        return None

    dock.pin("terminal", _noop)
    dock.pin("editor", _noop)
    check("pinned apps appear in pin order",
          dock.visible_names() == ["terminal", "editor"])
    check("pin is idempotent",
          (dock.pin("terminal", _noop), dock.visible_names())[1]
          == ["terminal", "editor"])

    dock.ensure("paint", _noop)
    check("ensure without running stays off the dock",
          dock.visible_names() == ["terminal", "editor"])

    dock.set_running("paint", True)
    check("running unpinned app appears after pins",
          dock.visible_names() == ["terminal", "editor", "paint"])
    check("running unpinned is not pinned",
          dock.is_pinned("paint") is False)

    dock.set_running("paint", False)
    check("unpinned icon leaves when it stops",
          dock.visible_names() == ["terminal", "editor"])

    dock.set_running("paint", True)
    dock.pin("paint")
    dock.set_running("paint", False)
    check("Keep in Dock keeps icon after quit",
          dock.visible_names() == ["terminal", "editor", "paint"]
          and dock.is_pinned("paint") is True)

    dock.unpin("paint")
    check("Remove from Dock on idle app hides it",
          dock.visible_names() == ["terminal", "editor"]
          and dock.is_pinned("paint") is False)

    dock.set_running("editor", True)
    dock.unpin("editor")
    check("Remove from Dock on running pinned app keeps it while running",
          "editor" in dock.visible_names()
          and dock.is_pinned("editor") is False)
    dock.set_running("editor", False)
    check("unpinned default app leaves after it stops",
          "editor" not in dock.visible_names())

    check("right-click is a context click", is_context_click(3, 0) is True)
    check("control-left is a context click",
          is_context_click(1, MOD_CTRL) is True)
    check("plain left click is not a context click",
          is_context_click(1, 0) is False)
    check("middle click is not a context click",
          is_context_click(2, 0) is False)

    check("desktop hit misses the menubar",
          desktop_background_hit(100, 10, 1024, 768) is False)
    check("desktop hit misses the dock",
          desktop_background_hit(100, 740, 1024, 768) is False)
    check("desktop hit misses windows",
          desktop_background_hit(50, 50, 1024, 768,
                                 window_rects=((40, 40, 200, 100),)) is False)
    check("desktop hit accepts empty wallpaper",
          desktop_background_hit(500, 400, 1024, 768) is True)

    apps = [
        _App("paint", "demo", "Paint"),
        _App("defender", "game", "Defender"),
        _App("terminal", "app", "Terminal"),
        _App("life", "demo", "Life"),
        _App("raiders", "game", "Raiders"),
    ]
    groups = launcher_groups(apps)
    check("launcher groups apps separately",
          [a.name for a in groups["app"]] == ["terminal"])
    check("launcher groups demos alphabetically",
          [a.name for a in groups["demo"]] == ["life", "paint"])
    check("launcher groups games alphabetically",
          [a.name for a in groups["game"]] == ["defender", "raiders"])

    launched = []
    items = desktop_popup_items(apps, launch=launched.append)
    labels = [it.label for it in items]
    check("desktop menu has Demos then Games sections",
          labels[:1] == ["Demos"]
          and "Games" in labels
          and labels.index("Demos") < labels.index("Games"))
    check("desktop menu lists demos before the Games header",
          labels.index("Life") < labels.index("Games")
          and labels.index("Paint") < labels.index("Games"))
    check("desktop menu lists games after the Games header",
          labels.index("Defender") > labels.index("Games")
          and labels.index("Raiders") > labels.index("Games"))
    check("desktop menu omits dock apps",
          "Terminal" not in labels and "terminal" not in labels)

    keep = []
    remove = []
    keep_items = dock_popup_items(False, on_keep=lambda: keep.append(1),
                                  on_remove=lambda: remove.append(1))
    check("unpinned dock menu offers Keep in Dock",
          [it.label for it in keep_items] == ["Keep in Dock"])
    pin_items = dock_popup_items(True, on_keep=lambda: keep.append(1),
                                 on_remove=lambda: remove.append(1))
    check("pinned dock menu offers Remove from Dock",
          [it.label for it in pin_items] == ["Remove from Dock"])

    clicked = []
    popup = Popup()
    popup.show(100, 200, [PopupItem("Paint", action=lambda: clicked.append("paint"))],
               total_w=1024)
    check("popup opens", popup.is_open is True)
    item_y = popup.item_rects[0][1] + popup.item_rects[0][3] // 2
    item_x = popup.item_rects[0][0] + 8
    check("popup click invokes action",
          popup.click(item_x, item_y) is True and clicked == ["paint"])
    check("popup closes after a successful click", popup.is_open is False)

    popup.show(100, 200, [PopupItem("Paint", action=lambda: clicked.append("x"))],
               total_w=1024)
    check("click outside popup dismisses without launching",
          popup.click(10, 10) is False and clicked == ["paint"]
          and popup.is_open is False)

    seeded = Dock()
    seed_pinned_from_registry(seeded, apps)
    check("registry seed pins only category=app",
          seeded.visible_names() == ["terminal"] and seeded.is_pinned("terminal"))

    for name in ("sprites", "defender", "pacmaze", "raiders"):
        src = open(os.path.join(ROOT, "apps", "demos", f"{name}.py")).read()
        check(f"{name} registers as category=game",
              'category="game"' in src)

    print()
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())

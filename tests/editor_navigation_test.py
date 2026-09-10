#!/usr/bin/env python3
"""Host tests for the shared editor's Emacs movement bindings."""

from __future__ import annotations

import importlib.util
import os
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_editor():
    kernel = types.ModuleType("kernel")
    kernel.__path__ = [os.path.join(ROOT, "kernel")]
    sys.modules["kernel"] = kernel

    fs_vfs = types.ModuleType("kernel.fs.vfs")
    fs_vfs.vfs = object()
    fs_vfs.OpenFlags = types.SimpleNamespace(
        RDONLY=1, WRONLY=2, CREAT=4, TRUNC=8)
    sys.modules["kernel.fs.vfs"] = fs_vfs

    compositor_mod = types.ModuleType("kernel.gui.compositor")
    compositor_mod.compositor = types.SimpleNamespace()
    compositor_mod.CompositorWindow = type("CompositorWindow", (), {})
    sys.modules["kernel.gui.compositor"] = compositor_mod

    surface_mod = types.ModuleType("kernel.gui.sdl2.surface")
    surface_mod.SDL_FillRect = lambda *args: None
    sys.modules["kernel.gui.sdl2.surface"] = surface_mod

    font_mod = types.ModuleType("kernel.display.font")
    font_mod.GLYPH_W = font_mod.GLYPH_H = 8
    sys.modules["kernel.display.font"] = font_mod

    apps = types.ModuleType("apps")
    apps.__path__ = [os.path.join(ROOT, "apps")]
    apps.registry = types.SimpleNamespace(register=lambda **kwargs: None)
    sys.modules["apps"] = apps
    icons = types.ModuleType("apps._icons")
    icons.editor_icon = lambda *args: None
    sys.modules["apps._icons"] = icons

    path = os.path.join(ROOT, "apps", "editor", "edwin.py")
    spec = importlib.util.spec_from_file_location("editor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load_editor()
    gui_input = sys.modules["kernel.gui.input"]
    editor = object.__new__(module.EditorView)
    editor.lines = ["one two.", "", "three four!", "five", "six"]
    editor.cy = editor.cx = editor.scroll = 0
    editor.rows = 2
    editor.message = ""
    editor.prompt_mode = None
    editor.ctrl_x_pending = False
    editor.dirty = False

    failed = 0

    def check(name, condition):
        nonlocal failed
        print(("  PASS  " if condition else "  FAIL  ") + name)
        failed += not bool(condition)

    def key(char, mods, text=""):
        return gui_input.Event(gui_input.EVENT_KEY_DOWN, ord(char), text, mods)

    print("editor_navigation_test")
    editor.on_event(key("f", gui_input.MOD_ALT))
    check("Alt-f moves forward by a word", (editor.cy, editor.cx) == (0, 3))
    editor.on_event(key("f", gui_input.MOD_META))
    check("Meta-f uses the same binding", (editor.cy, editor.cx) == (0, 7))
    editor.on_event(key("a", gui_input.MOD_ALT))
    check("Meta-a moves to sentence start", (editor.cy, editor.cx) == (0, 0))
    editor.on_event(key("e", gui_input.MOD_META))
    check("Meta-e moves to the next sentence", (editor.cy, editor.cx) == (2, 0))

    editor.cy = editor.cx = 0
    editor.on_event(key("v", gui_input.MOD_CTRL, "\x16"))
    check("Control-v moves forward a page", editor.cy == 2)
    editor.on_event(key("v", gui_input.MOD_ALT))
    check("Meta-v moves backward a page", editor.cy == 0)

    editor.on_event(key(".", gui_input.MOD_META | gui_input.MOD_SHIFT, ">"))
    check("Meta-> moves to buffer end", (editor.cy, editor.cx) == (4, 3))
    editor.on_event(key(",", gui_input.MOD_ALT | gui_input.MOD_SHIFT, "<"))
    check("Alt-< moves to buffer start", (editor.cy, editor.cx) == (0, 0))

    editor.on_event(key("]", gui_input.MOD_META | gui_input.MOD_SHIFT, "}"))
    check("Meta-} moves forward a paragraph", (editor.cy, editor.cx) == (2, 0))
    editor.scroll = 0
    editor.on_event(key("l", gui_input.MOD_CTRL, "\x0c"))
    check("Control-l recenters around point", editor.scroll == 1)

    print(f"\n{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Host tests for configurable desktop shortcut matching."""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
kernel = types.ModuleType("kernel")
kernel.__path__ = [os.path.join(ROOT, "kernel")]
gui = types.ModuleType("kernel.gui")
gui.__path__ = [os.path.join(ROOT, "kernel", "gui")]
sys.modules["kernel"] = kernel
sys.modules["kernel.gui"] = gui

from kernel.gui import input as gui_input
from kernel.gui import keybindings


def event(code, mods=0):
    return gui_input.Event(gui_input.EVENT_KEY_DOWN, code=code, mods=mods)


def main() -> int:
    keybindings.reset()
    assert keybindings.action_for(event(gui_input.KEY_F1)) == "keybindings"
    assert keybindings.action_for(event(gui_input.KEY_F2)) == "source"
    assert keybindings.action_for(event(gui_input.KEY_TAB)) == "next_window"
    assert keybindings.action_for(event(gui_input.KEY_TAB,
                                        gui_input.MOD_SHIFT)) == "previous_window"
    keybindings.set_binding("source", ord("s"),
                            gui_input.MOD_CTRL | gui_input.MOD_ALT)
    assert keybindings.action_for(event(gui_input.KEY_F2)) is None
    assert keybindings.action_for(event(ord("s"), gui_input.MOD_CTRL |
                                       gui_input.MOD_ALT)) == "source"
    source = [row for row in keybindings.all_bindings()
              if row.action == "source"][0]
    assert keybindings.describe(source) == "Ctrl+Alt+S"
    keybindings.reset("source")
    assert keybindings.action_for(event(gui_input.KEY_F2)) == "source"
    print("keybindings_test\n  PASS  defaults, remapping, exact modifiers, reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

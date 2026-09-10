"""Desktop-wide, user-configurable keyboard shortcuts."""

from __future__ import annotations

from dataclasses import dataclass

from kernel.gui import input as gui_input


RELEVANT_MODS = (gui_input.MOD_SHIFT | gui_input.MOD_CTRL |
                 gui_input.MOD_ALT | gui_input.MOD_META)


@dataclass
class Binding:
    action: str
    label: str
    code: int
    mods: int = 0


_DEFAULTS = (
    Binding("keybindings", "Open Keybindings", gui_input.KEY_F1),
    Binding("source", "View focused source", gui_input.KEY_F2),
    Binding("next_window", "Next window", gui_input.KEY_TAB),
    Binding("previous_window", "Previous window", gui_input.KEY_TAB,
            gui_input.MOD_SHIFT),
)
_bindings = {row.action: Binding(row.action, row.label, row.code, row.mods)
             for row in _DEFAULTS}
_SETTINGS_PATH = "/home/.pythonos-keybindings"


def all_bindings() -> list[Binding]:
    return list(_bindings.values())


def set_binding(action: str, code: int, mods: int = 0) -> None:
    row = _bindings[action]
    code = int(code)
    mods = int(mods) & RELEVANT_MODS
    # Swapping avoids silently making an action unreachable when the user
    # chooses a combination already assigned to another action.
    for other in _bindings.values():
        if other is not row and other.code == code and other.mods == mods:
            other.code, other.mods = row.code, row.mods
            break
    row.code, row.mods = code, mods


def reset(action: str | None = None) -> None:
    for default in _DEFAULTS:
        if action is None or action == default.action:
            set_binding(default.action, default.code, default.mods)


def action_for(event) -> str | None:
    if event.kind != gui_input.EVENT_KEY_DOWN:
        return None
    mods = int(event.mods) & RELEVANT_MODS
    for row in _bindings.values():
        if row.code == event.code and row.mods == mods:
            return row.action
    return None


def describe(row: Binding) -> str:
    parts = []
    if row.mods & gui_input.MOD_CTRL:
        parts.append("Ctrl")
    if row.mods & gui_input.MOD_ALT:
        parts.append("Alt")
    if row.mods & gui_input.MOD_META:
        parts.append("Meta")
    if row.mods & gui_input.MOD_SHIFT:
        parts.append("Shift")
    names = {
        gui_input.KEY_TAB: "Tab", gui_input.KEY_ENTER: "Enter",
        gui_input.KEY_ESC: "Esc", gui_input.KEY_SPACE: "Space",
        gui_input.KEY_LEFT: "Left", gui_input.KEY_RIGHT: "Right",
        gui_input.KEY_UP: "Up", gui_input.KEY_DOWN: "Down",
        gui_input.KEY_HOME: "Home", gui_input.KEY_END: "End",
        gui_input.KEY_PAGE_UP: "PageUp", gui_input.KEY_PAGE_DOWN: "PageDown",
    }
    for number in range(1, 13):
        names[getattr(gui_input, "KEY_F" + str(number))] = "F" + str(number)
    key = names.get(row.code)
    if key is None:
        key = chr(row.code).upper() if 32 <= row.code < 127 else str(row.code)
    parts.append(key)
    return "+".join(parts)


async def load() -> None:
    """Load persistent overrides; missing or malformed settings are harmless."""
    from kernel.fs.vfs import OpenFlags, vfs
    try:
        fd = await vfs.open(_SETTINGS_PATH, OpenFlags.RDONLY)
    except OSError:
        return
    data = bytearray()
    try:
        while True:
            chunk = await vfs.read(fd, 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 8192:
                return
    finally:
        vfs.close(fd)
    try:
        for line in data.decode("ascii").splitlines():
            action, code, mods = line.split(":", 2)
            if action in _bindings:
                set_binding(action, int(code), int(mods))
    except (ValueError, UnicodeError):
        reset()


async def save() -> None:
    """Persist the current mapping on the mounted /home filesystem."""
    from kernel.fs.vfs import OpenFlags, vfs
    payload = "".join(f"{row.action}:{row.code}:{row.mods}\n"
                      for row in all_bindings()).encode("ascii")
    try:
        fd = await vfs.open(_SETTINGS_PATH,
                            OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
        try:
            offset = 0
            while offset < len(payload):
                written = await vfs.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("short keybindings write")
                offset += written
        finally:
            vfs.close(fd)
    except OSError:
        pass

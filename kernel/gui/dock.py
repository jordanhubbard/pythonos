"""kernel.gui.dock — macOS-style dock membership and desktop launch menus.

The compositor owns painting and input routing. This module is the
single source of truth for:

* which apps are pinned vs merely running
* whether a mouse event is a context-click
* whether a click landed on empty wallpaper
* Demos / Games grouping for the desktop and system menus
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from kernel.gui.input import MOD_CTRL


# Match kernel.gui.menubar / compositor geometry so hit-tests agree
# with what the compositor paints.
MENU_BAR_H = 22
DOCK_H = 72
POPUP_ITEM_H = 22
POPUP_SEPARATOR_H = 8
POPUP_PAD_X = 14
POPUP_PAD_Y = 6
POPUP_MIN_W = 120
GLYPH_W = 8
MOUSE_LEFT = 1
MOUSE_RIGHT = 3


@dataclass
class DockItem:
    name: str
    entry: Callable | None = None
    icon_factory: Callable | None = None
    pinned: bool = False
    running: bool = False


@dataclass
class PopupItem:
    label: str
    action: Callable | None = None
    separator: bool = False
    enabled: bool = True


class Dock:
    """Pinned icons plus currently-running unpinned transients."""

    def __init__(self) -> None:
        self._items: dict[str, DockItem] = {}
        self._pin_order: list[str] = []
        self._transient_order: list[str] = []

    def _item(self, name: str, entry=None, icon_factory=None) -> DockItem:
        item = self._items.get(name)
        if item is None:
            item = DockItem(name=name, entry=entry, icon_factory=icon_factory)
            self._items[name] = item
        else:
            if entry is not None:
                item.entry = entry
            if icon_factory is not None:
                item.icon_factory = icon_factory
        return item

    def pin(self, name: str, entry=None, icon_factory=None) -> None:
        item = self._item(name, entry, icon_factory)
        item.pinned = True
        if name in self._transient_order:
            self._transient_order.remove(name)
        if name not in self._pin_order:
            self._pin_order.append(name)

    def unpin(self, name: str) -> None:
        item = self._items.get(name)
        if item is None:
            return
        item.pinned = False
        if name in self._pin_order:
            self._pin_order.remove(name)
        if item.running and name not in self._transient_order:
            self._transient_order.append(name)

    def ensure(self, name: str, entry=None, icon_factory=None) -> DockItem:
        return self._item(name, entry, icon_factory)

    def set_running(self, name: str, running: bool) -> None:
        item = self._items.get(name)
        if item is None:
            if not running:
                return
            item = self._item(name)
        item.running = bool(running)
        if item.pinned:
            return
        if item.running:
            if name not in self._transient_order:
                self._transient_order.append(name)
        elif name in self._transient_order:
            self._transient_order.remove(name)

    def is_pinned(self, name: str) -> bool:
        item = self._items.get(name)
        return bool(item and item.pinned)

    def get(self, name: str) -> DockItem | None:
        return self._items.get(name)

    def visible(self) -> list[DockItem]:
        names = list(self._pin_order)
        for name in self._transient_order:
            if name not in names:
                names.append(name)
        return [self._items[n] for n in names if n in self._items]

    def visible_names(self) -> list[str]:
        return [item.name for item in self.visible()]


def is_context_click(button: int, mods: int = 0) -> bool:
    """Right-click, two-finger click (button 3), or control-left."""
    if button == MOUSE_RIGHT:
        return True
    if button == MOUSE_LEFT and (mods & MOD_CTRL):
        return True
    return False


def desktop_background_hit(x: int, y: int, desk_w: int, desk_h: int,
                           menubar_h: int = MENU_BAR_H,
                           dock_h: int = DOCK_H,
                           window_rects=()) -> bool:
    """True when (x, y) is empty wallpaper — not menubar, dock, or a window."""
    if y < menubar_h:
        return False
    if y >= desk_h - dock_h:
        return False
    if x < 0 or y < 0 or x >= desk_w or y >= desk_h:
        return False
    for wx, wy, ww, wh in window_rects:
        if wx <= x < wx + ww and wy <= y < wy + wh:
            return False
    return True


def launcher_groups(apps) -> dict[str, list]:
    """Split registry entries into app / demo / game lists, sorted by name."""
    groups = {"app": [], "demo": [], "game": []}
    for info in apps:
        cat = getattr(info, "category", "app") or "app"
        if cat not in groups:
            continue
        groups[cat].append(info)
    for cat in groups:
        groups[cat].sort(key=lambda a: a.name)
    return groups


def seed_pinned_from_registry(dock: Dock, apps) -> None:
    for info in apps:
        if getattr(info, "category", "app") == "app":
            dock.pin(info.name, getattr(info, "entry", None),
                     getattr(info, "icon_factory", None))


def desktop_popup_items(apps, launch: Callable[[str], None]) -> list[PopupItem]:
    groups = launcher_groups(apps)
    items: list[PopupItem] = [PopupItem("Demos", enabled=False)]
    if groups["demo"]:
        for info in groups["demo"]:
            label = info.description or info.name
            items.append(PopupItem(label, action=_bind_launch(launch, info.name)))
    else:
        items.append(PopupItem("(none)", enabled=False))
    items.append(PopupItem("", separator=True))
    items.append(PopupItem("Games", enabled=False))
    if groups["game"]:
        for info in groups["game"]:
            label = info.description or info.name
            items.append(PopupItem(label, action=_bind_launch(launch, info.name)))
    else:
        items.append(PopupItem("(none)", enabled=False))
    return items


def dock_popup_items(pinned: bool, on_keep, on_remove) -> list[PopupItem]:
    if pinned:
        return [PopupItem("Remove from Dock", action=on_remove)]
    return [PopupItem("Keep in Dock", action=on_keep)]


def _bind_launch(launch: Callable[[str], None], name: str):
    return lambda: launch(name)


class Popup:
    """Positioned list of :class:`PopupItem` rows. Layout uses bitmap
    metrics so host tests and the compositor hit-test agree; the
    compositor may still paint with TTF inside the same row rects."""

    def __init__(self) -> None:
        self.items: list[PopupItem] = []
        self.item_rects: list[tuple[int, int, int, int]] = []
        self.anchor: tuple[int, int, int, int] | None = None
        self.hot_index: int = -1
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def hide(self) -> None:
        self._open = False
        self.hot_index = -1
        self.item_rects = []
        self.anchor = None
        self.items = []

    def show(self, x: int, y: int, items: list[PopupItem],
             total_w: int) -> None:
        self.items = list(items)
        self.hot_index = -1
        text_w = 0
        for it in self.items:
            if it.separator:
                continue
            tw = max(1, len(it.label)) * GLYPH_W
            if tw > text_w:
                text_w = tw
        w = max(POPUP_MIN_W, text_w + POPUP_PAD_X * 2)
        h = POPUP_PAD_Y * 2
        for it in self.items:
            h += POPUP_SEPARATOR_H if it.separator else POPUP_ITEM_H
        if x + w > total_w:
            x = max(0, total_w - w - 4)
        y = max(0, y)
        self.anchor = (x, y, w, h)
        self.item_rects = []
        cy = y + POPUP_PAD_Y
        for it in self.items:
            row_h = POPUP_SEPARATOR_H if it.separator else POPUP_ITEM_H
            self.item_rects.append((x, cy, w, row_h))
            cy += row_h
        self._open = True

    def contains(self, x: int, y: int) -> bool:
        if not self._open or self.anchor is None:
            return False
        ax, ay, aw, ah = self.anchor
        return ax <= x < ax + aw and ay <= y < ay + ah

    def item_index_at(self, x: int, y: int) -> int:
        if not self.contains(x, y):
            return -1
        for i, (ix, iy, iw, ih) in enumerate(self.item_rects):
            if ix <= x < ix + iw and iy <= y < iy + ih:
                return i
        return -1

    def on_move(self, x: int, y: int) -> bool:
        if not self._open:
            return False
        new_hot = -1
        idx = self.item_index_at(x, y)
        if idx >= 0:
            it = self.items[idx]
            if not it.separator and it.enabled:
                new_hot = idx
        if new_hot != self.hot_index:
            self.hot_index = new_hot
            return True
        return False

    def click(self, x: int, y: int) -> bool:
        """Activate the item under (x, y). Returns True if an enabled
        action ran. Clicks outside dismiss the popup and return False."""
        if not self._open:
            return False
        idx = self.item_index_at(x, y)
        if idx < 0:
            self.hide()
            return False
        it = self.items[idx]
        self.hide()
        if it.separator or not it.enabled or it.action is None:
            return False
        it.action()
        return True

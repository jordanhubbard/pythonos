"""kernel.gui.desktop — Seed the dock and system menus from the registry."""

from __future__ import annotations

import kernel.log as log
from kernel.gui.dock import launcher_groups, seed_pinned_from_registry
from kernel.gui.menubar import Menu, MenuItem


def seed_system_menus(compositor, registry) -> None:
    """Build PythonOS / Apps / Demos / Games from the app registry."""

    def _about() -> None:
        if registry.get("about") is not None:
            compositor.launch_app("about")
        else:
            log.info("PythonOS — Python is the kernel.")

    def _launcher(app_name):
        return lambda: compositor.launch_app(app_name)

    def _items(infos):
        rows = [
            MenuItem(info.description or info.name, action=_launcher(info.name))
            for info in infos
        ]
        return rows or [MenuItem("(none)", enabled=False)]

    groups = launcher_groups(registry.list_apps())
    compositor._menubar.set_system_menus([
        Menu("PythonOS", [
            MenuItem("About PythonOS", action=_about),
            MenuItem.sep(),
            MenuItem("Version: 3.14.0a0", enabled=False),
        ]),
        Menu("Apps", _items(groups["app"])),
        Menu("Demos", _items(groups["demo"])),
        Menu("Games", _items(groups["game"])),
    ])


def seed_desktop(compositor, registry) -> None:
    """Pin bundled apps and fill the system menu bar."""
    seed_pinned_from_registry(compositor._dock, registry.list_apps())
    seed_system_menus(compositor, registry)

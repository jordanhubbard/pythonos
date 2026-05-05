"""apps.registry — In-memory list of available GUI apps.

Apps register at import time via :func:`register`. The
``pythonos_gui`` launcher iterates over :func:`list_apps` to populate
its dock.
"""

from dataclasses import dataclass
from typing import Callable, Awaitable


@dataclass
class AppInfo:
    name:          str
    description:   str
    entry:         Callable[..., Awaitable[None]]
    # Optional zero-arg factory returning a 48x48 SDL_Surface — the
    # dock calls it lazily once the bridge is up. None falls back to
    # a generated colored square with the app name.
    icon_factory:  Callable[[], object] | None = None
    # "app"  — full apps. Surfaced in the dock and the System > Apps menu.
    # "demo" — graphics/audio demos. Surfaced only in the System > Demos
    #          menu (not the dock), matching the macOS dock convention
    #          where demos / sample apps live in a separate launcher.
    category:      str = "app"
    # Per-app menubar declarations — each entry is a kernel.gui.menubar.Menu.
    # The compositor appends these to the system menubar when this app
    # has focus. Empty list = no app-specific menus.
    menus:         list = None  # type: ignore[assignment]


_apps: dict[str, AppInfo] = {}


def register(name: str, description: str, entry,
              icon_factory=None, *, category: str = "app",
              menus=None) -> None:
    _apps[name] = AppInfo(name=name, description=description, entry=entry,
                           icon_factory=icon_factory,
                           category=category,
                           menus=list(menus) if menus else [])


def list_apps() -> list[AppInfo]:
    return list(_apps.values())


def get(name: str) -> AppInfo | None:
    return _apps.get(name)

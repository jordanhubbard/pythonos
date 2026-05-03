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


_apps: dict[str, AppInfo] = {}


def register(name: str, description: str, entry,
              icon_factory=None) -> None:
    _apps[name] = AppInfo(name=name, description=description, entry=entry,
                           icon_factory=icon_factory)


def list_apps() -> list[AppInfo]:
    return list(_apps.values())


def get(name: str) -> AppInfo | None:
    return _apps.get(name)

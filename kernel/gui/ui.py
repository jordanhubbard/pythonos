"""Small, teachable Python view hierarchy over the SDL-compatible surface.

Applications normally use these classes. Direct ``kernel.gui.sdl2`` drawing
remains available for examples that need pixel-level control.
"""

from __future__ import annotations


class UIElement:
    """Base of every high-level UI object."""

    def __init__(self, *, visible: bool = True, enabled: bool = True) -> None:
        self.visible = visible
        self.enabled = enabled
        self.parent = None

    def invalidate(self) -> None:
        """Ask the containing compositor window to repaint."""
        host = getattr(self, "host", None)
        if host is not None and hasattr(host, "dirty"):
            host.dirty = True
            return
        node = self
        while node is not None:
            if hasattr(node, "dirty"):
                node.dirty = True
                return
            node = getattr(node, "parent", None)


class View(UIElement):
    """A rectangular drawable region.

    Override :meth:`draw` and :meth:`on_event` for custom teaching projects.
    Coordinates are relative to the window body.
    """

    def __init__(self, x: int = 0, y: int = 0, w: int = 0, h: int = 0,
                 *, background: int | None = None, host=None) -> None:
        super().__init__()
        self.x, self.y, self.w, self.h = x, y, w, h
        self.background = background
        self.host = host

    @property
    def surface(self):
        own = getattr(self, "_surface", None)
        if own is not None:
            return own
        if self.host is not None:
            return self.host.surface
        node = self.parent
        while node is not None:
            candidate = getattr(node, "surface", None)
            if candidate is not None:
                return candidate
            node = getattr(node, "parent", None)
        return None

    @surface.setter
    def surface(self, value) -> None:
        self._surface = value

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def fill(self, color: int, x: int | None = None, y: int | None = None,
             w: int | None = None, h: int | None = None) -> None:
        surface = self.surface
        if surface is None:
            return
        surface._fill_rect(self.x if x is None else x,
                           self.y if y is None else y,
                           self.w if w is None else w,
                           self.h if h is None else h, color)
        self.invalidate()

    def draw(self, surface) -> None:
        if self.visible and self.background is not None:
            surface._fill_rect(self.x, self.y, self.w, self.h,
                               self.background)

    def on_event(self, event) -> bool:
        """Handle an event; return True when it was consumed."""
        return False


class Container(View):
    """A view that owns child views."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.children: list[View] = []

    def add(self, child: View):
        child.parent = self
        if child.host is None:
            child.host = self.host
        self.children.append(child)
        self.invalidate()
        return child

    def remove(self, child: View) -> None:
        if child in self.children:
            self.children.remove(child)
            child.parent = None
            self.invalidate()

    def draw(self, surface) -> None:
        super().draw(surface)
        for child in self.children:
            if child.visible:
                child.draw(surface)

    def on_event(self, event) -> bool:
        mouse = getattr(event, "kind", 0) in (3, 4, 5, 6)
        for child in reversed(self.children):
            if not child.visible or not child.enabled:
                continue
            if mouse and not child.contains(event.x, event.y):
                continue
            if child.on_event(event):
                return True
        return False


class Panel(Container):
    """A colored container used to group controls."""


class Label(View):
    """One line of bitmap-font text."""

    def __init__(self, text: str, *args, color: int = 0xFFFFFF,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text = text
        self.color = color

    def draw(self, surface) -> None:
        super().draw(surface)
        if self.visible:
            surface.draw_text(self.x, self.y, self.text, fg=self.color,
                              bg=self.background or 0)


class Button(Label):
    """A label that invokes ``action`` when clicked."""

    def __init__(self, text: str, *args, action=None, **kwargs) -> None:
        super().__init__(text, *args, **kwargs)
        self.action = action

    def on_event(self, event) -> bool:
        if getattr(event, "kind", 0) == 4 and getattr(event, "code", 0) == 1:
            if self.action is not None:
                self.action()
            self.invalidate()
            return True
        return False


class TextView(View):
    """Fixed-pitch text view shared by editors and terminals."""

    def text_at(self, x: int, y: int, text: str, *, color: int = 0xCCCCCC,
                background: int = 0x101820) -> None:
        surface = self.surface
        if surface is not None:
            surface.draw_text(x, y, text, fg=color, bg=background)
            self.invalidate()


class ListView(TextView):
    """Text view with conventional selection and scrolling state."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.items: list = []
        self.selected = 0
        self.scroll_top = 0

    def move_selection(self, delta: int) -> None:
        if self.items:
            self.selected = max(0, min(len(self.items) - 1,
                                       self.selected + delta))
            self.invalidate()

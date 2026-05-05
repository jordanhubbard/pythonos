"""kernel.gui.menubar — macOS-style menu bar at the top of the desktop.

The compositor reserves :data:`MENU_BAR_H` pixels at the top of its
output surface for this bar. When no app has focus, the bar shows a
*system menu* (Apple-style menus + a list of registered apps and demos).
When an app does have focus, its declared menus are appended after the
system menu, replacing nothing — the system menu always shows.

The bar holds modal state (an open dropdown), draws itself on every
compositor frame, and consumes mouse clicks that land in its hit area.
The compositor delegates click routing via :meth:`MenuBar.on_mouse_down`
before falling back to dock / window handling.
"""

import kernel.log as log
from kernel.display.font import GLYPH_W, GLYPH_H


# ── Geometry / palette ─────────────────────────────────────────────────────

MENU_BAR_H        = 20

MENU_BAR_BG       = 0x1B1F2A
MENU_BAR_FG       = 0xE0E0E0
MENU_BAR_HOT_BG   = 0x4060A0
MENU_BAR_HOT_FG   = 0xFFFFFF
MENU_TITLE_PAD_X  = 12
MENU_TITLE_GAP    = 8

DROPDOWN_BG       = 0x252837
DROPDOWN_FG       = 0xCCCCCC
DROPDOWN_HOT_BG   = 0x4060A0
DROPDOWN_HOT_FG   = 0xFFFFFF
DROPDOWN_BORDER   = 0x404858
DROPDOWN_PAD_X    = 12
DROPDOWN_PAD_Y    = 4
ITEM_H            = 18
SEPARATOR_H       = 6
SEPARATOR_FG      = 0x4A506A
DISABLED_FG       = 0x707080


# ── Data model ─────────────────────────────────────────────────────────────

class MenuItem:
    """One row inside a dropdown menu.

    ``action`` is a zero-arg callable invoked when the item is clicked.
    Set ``separator=True`` for a divider row (action and label ignored).
    Set ``enabled=False`` for a greyed-out non-clickable row.
    """

    __slots__ = ("label", "action", "separator", "enabled")

    def __init__(self, label: str, action=None, *,
                 separator: bool = False, enabled: bool = True) -> None:
        self.label     = label
        self.action    = action
        self.separator = separator
        self.enabled   = enabled

    @classmethod
    def sep(cls) -> "MenuItem":
        return cls("", separator=True)


class Menu:
    """A top-level menu — a title in the bar plus its dropdown items."""

    __slots__ = ("title", "items")

    def __init__(self, title: str, items: list) -> None:
        self.title = title
        self.items = list(items)


# ── MenuBar ────────────────────────────────────────────────────────────────

class MenuBar:
    """Owns the system-menu list, the focused app's menu list (if any),
    and the open-dropdown UI state. Stateless w.r.t. rendering target —
    every redraw recomputes layout into ``self._title_rects`` and
    ``self._item_rects`` for hit testing."""

    def __init__(self) -> None:
        self._system_menus: list[Menu] = []
        self._app_menus:    list[Menu] = []
        # Currently-open menu index in self._all_menus(), or -1 = closed.
        self._open_idx: int = -1
        self._hot_item_idx: int = -1
        # Layout cached at draw time so hit-test sees the same geometry
        # the user is looking at.
        self._title_rects: list[tuple[int, int, int, int]] = []   # (x, y, w, h) per menu
        self._dropdown_anchor: tuple[int, int, int, int] | None = None  # x, y, w, h
        self._item_rects: list[tuple[int, int, int, int]] = []    # (x, y, w, h) per item
        # Text to render right-aligned in the menu bar (typically a
        # clock / uptime). The compositor refreshes this each frame; the
        # menu bar itself is otherwise stateless w.r.t. time.
        self._right_text: str = ""

    # ── Configuration ─────────────────────────────────────────────────

    def set_system_menus(self, menus: list) -> None:
        self._system_menus = list(menus or [])
        self._open_idx = -1
        self._hot_item_idx = -1

    def set_app_menus(self, menus: list) -> None:
        """Set the focused app's menus. Pass [] for desktop / no app."""
        self._app_menus = list(menus or [])
        self._open_idx = -1
        self._hot_item_idx = -1

    def set_right_text(self, text: str) -> None:
        """Update the right-aligned text drawn at the trailing edge of
        the bar (clock, uptime, status). Empty string disables it."""
        self._right_text = text or ""

    def all_menus(self) -> list:
        return self._system_menus + self._app_menus

    @property
    def is_open(self) -> bool:
        return self._open_idx >= 0

    def close(self) -> None:
        self._open_idx = -1
        self._hot_item_idx = -1

    # ── Drawing ───────────────────────────────────────────────────────

    def render(self, surface, total_w: int) -> int:
        """Draw the bar (and any open dropdown) onto ``surface``.

        ``surface`` is anything that quacks like
        :class:`kernel.gui.sdl2.surface.SDL_Surface` — we use ``_fill_rect``
        and ``draw_text``, which dispatch through the bridge in host-backed
        mode and through guest pixels otherwise. Returns the height of the
        bar so the compositor knows how much vertical space it consumes.
        """
        # Bar background.
        surface._fill_rect(0, 0, total_w, MENU_BAR_H, MENU_BAR_BG)

        # Lay out titles left to right.
        self._title_rects = []
        x = MENU_TITLE_GAP
        menus = self.all_menus()
        for i, m in enumerate(menus):
            tw = max(1, len(m.title)) * GLYPH_W
            box_w = tw + MENU_TITLE_PAD_X
            box_x = x - MENU_TITLE_PAD_X // 2
            if i == self._open_idx:
                surface._fill_rect(box_x, 0, box_w, MENU_BAR_H, MENU_BAR_HOT_BG)
                fg, bg = MENU_BAR_HOT_FG, MENU_BAR_HOT_BG
            else:
                fg, bg = MENU_BAR_FG, MENU_BAR_BG
            text_y = max(0, (MENU_BAR_H - GLYPH_H) // 2)
            surface.draw_text(x, text_y, m.title, fg=fg, bg=bg)
            self._title_rects.append((box_x, 0, box_w, MENU_BAR_H))
            x += box_w + MENU_TITLE_GAP

        # Right-aligned text (clock / uptime / status).
        if self._right_text:
            tw = len(self._right_text) * GLYPH_W
            rx = total_w - tw - MENU_TITLE_GAP
            ry = max(0, (MENU_BAR_H - GLYPH_H) // 2)
            surface.draw_text(rx, ry, self._right_text,
                               fg=MENU_BAR_FG, bg=MENU_BAR_BG)

        # Render the open dropdown last so it sits on top of nothing
        # (windows are drawn after the menu bar in the compositor).
        self._dropdown_anchor = None
        self._item_rects = []
        if self._open_idx >= 0 and self._open_idx < len(menus):
            self._render_dropdown(surface, menus[self._open_idx],
                                   self._title_rects[self._open_idx][0],
                                   MENU_BAR_H, total_w)

        return MENU_BAR_H

    def _measure_dropdown(self, menu: Menu) -> tuple[int, int]:
        """Return (width, height) for ``menu``'s dropdown panel."""
        text_w = 0
        for it in menu.items:
            if it.separator:
                continue
            tw = len(it.label) * GLYPH_W
            if tw > text_w:
                text_w = tw
        w = text_w + DROPDOWN_PAD_X * 2
        h = DROPDOWN_PAD_Y * 2
        for it in menu.items:
            h += SEPARATOR_H if it.separator else ITEM_H
        return (max(w, 80), h)

    def _render_dropdown(self, surface, menu: Menu,
                          anchor_x: int, anchor_y: int, total_w: int) -> None:
        w, h = self._measure_dropdown(menu)
        # Snap the panel inside the visible area.
        x = anchor_x
        if x + w > total_w:
            x = max(0, total_w - w - 4)
        y = anchor_y

        # Drop-shadow: 2-px offset, dim — purely cosmetic.
        surface._fill_rect(x + 2, y + 2, w, h, 0x000000)
        # Panel.
        surface._fill_rect(x, y, w, h, DROPDOWN_BG)
        # Border (1 px).
        surface._fill_rect(x, y, w, 1, DROPDOWN_BORDER)
        surface._fill_rect(x, y + h - 1, w, 1, DROPDOWN_BORDER)
        surface._fill_rect(x, y, 1, h, DROPDOWN_BORDER)
        surface._fill_rect(x + w - 1, y, 1, h, DROPDOWN_BORDER)

        self._dropdown_anchor = (x, y, w, h)
        cy = y + DROPDOWN_PAD_Y
        for i, it in enumerate(menu.items):
            if it.separator:
                surface._fill_rect(x + 4, cy + SEPARATOR_H // 2,
                                    w - 8, 1, SEPARATOR_FG)
                self._item_rects.append((x, cy, w, SEPARATOR_H))
                cy += SEPARATOR_H
                continue
            row_h = ITEM_H
            row_w = w - 2
            row_x = x + 1
            if i == self._hot_item_idx and it.enabled:
                surface._fill_rect(row_x, cy, row_w, row_h, DROPDOWN_HOT_BG)
                fg, bg = DROPDOWN_HOT_FG, DROPDOWN_HOT_BG
            else:
                fg = DROPDOWN_FG if it.enabled else DISABLED_FG
                bg = DROPDOWN_BG
            surface.draw_text(x + DROPDOWN_PAD_X,
                               cy + (row_h - GLYPH_H) // 2,
                               it.label, fg=fg, bg=bg)
            self._item_rects.append((x, cy, w, row_h))
            cy += row_h

    # ── Hit testing / event handling ──────────────────────────────────

    def hit_zone(self, x: int, y: int) -> str:
        """Return ``'title'`` (mouse is on a top-level title),
        ``'item'`` (in an open dropdown), or ``'outside'``. Used by the
        compositor to decide whether to forward a click."""
        if y < MENU_BAR_H:
            return "title"
        if self._open_idx >= 0 and self._dropdown_anchor:
            ax, ay, aw, ah = self._dropdown_anchor
            if ax <= x < ax + aw and ay <= y < ay + ah:
                return "item"
        return "outside"

    def on_mouse_move(self, x: int, y: int) -> bool:
        """Track the hovered item inside an open dropdown. Returns True
        if the hover state changed and a redraw is needed."""
        if self._open_idx < 0:
            return False
        new_hot = -1
        if self.hit_zone(x, y) == "item":
            for i, (ix, iy, iw, ih) in enumerate(self._item_rects):
                if ix <= x < ix + iw and iy <= y < iy + ih:
                    if i < len(self._all_menus_items_for_open()):
                        item = self._all_menus_items_for_open()[i]
                        if not item.separator and item.enabled:
                            new_hot = i
                    break
        if new_hot != self._hot_item_idx:
            self._hot_item_idx = new_hot
            return True
        return False

    def _all_menus_items_for_open(self) -> list:
        if self._open_idx < 0:
            return []
        return self.all_menus()[self._open_idx].items

    def on_mouse_down(self, x: int, y: int) -> bool:
        """Handle a mouse-down at (x, y). Returns True if the menu bar
        consumed the event and the compositor should NOT route it on."""
        zone = self.hit_zone(x, y)
        if zone == "title":
            for i, (tx, ty, tw, th) in enumerate(self._title_rects):
                if tx <= x < tx + tw:
                    if self._open_idx == i:
                        self.close()
                    else:
                        self._open_idx = i
                        self._hot_item_idx = -1
                    return True
            # Click landed in the bar but between titles — close any open menu.
            if self._open_idx >= 0:
                self.close()
                return True
            return False
        if zone == "item":
            for i, (ix, iy, iw, ih) in enumerate(self._item_rects):
                if ix <= x < ix + iw and iy <= y < iy + ih:
                    items = self._all_menus_items_for_open()
                    if i >= len(items):
                        break
                    item = items[i]
                    self.close()
                    if item.separator or not item.enabled:
                        return True
                    if item.action is not None:
                        try:
                            item.action()
                        except Exception as e:
                            log.warn(f"menu: action '{item.label}' failed: {e}")
                    return True
            return True
        # Outside — clicking anywhere else closes an open menu.
        if self._open_idx >= 0:
            self.close()
            return True
        return False

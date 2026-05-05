"""kernel.gui.menubar — macOS-style menu bar at the top of the desktop.

The compositor reserves :data:`MENU_BAR_H` pixels at the top of its
output surface for this bar. When no app has focus, the bar shows a
*system menu* (Apple-style menus + a list of registered apps and demos).
When an app does have focus, its declared menus are appended after the
system menu, replacing nothing — the system menu always shows.

Text rendering is handled by libSDL2_ttf on the host (via the mirror-SDL
bridge) — proper anti-aliased proportional glyphs, with surfaces cached
per ``(text, color)`` to keep per-frame cost down to a single host blit
each. If the bridge can't load a default font (no system fonts on the
host, headless boot), the bar falls back to the kernel's 8×8 bitmap
font automatically; layout uses fixed-pitch math in that mode.

The bar holds modal state (an open dropdown), draws itself on every
compositor frame, and consumes mouse clicks that land in its hit area.
The compositor delegates click routing via :meth:`MenuBar.on_mouse_down`
before falling back to dock / window handling.
"""

import kernel.log as log
from kernel.display.font import GLYPH_W, GLYPH_H


# ── Geometry / palette ─────────────────────────────────────────────────────

# Bar is sized for ~13pt anti-aliased text + breathing room. The bitmap
# fallback (8×8) sits comfortably inside the same band.
MENU_BAR_H        = 22

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
DROPDOWN_PAD_X    = 14
DROPDOWN_PAD_Y    = 6
ITEM_H            = 22
SEPARATOR_H       = 8
SEPARATOR_FG      = 0x4A506A
DISABLED_FG       = 0x707080
TTF_FONT_PT       = 13


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
        # SDL_ttf font + cached pre-rendered text surfaces, keyed by
        # ``(text, color)``. Each render reuses these so the per-frame
        # cost is ~one host blit per visible label. ``_font_state`` is
        # tri-valued: None = not yet attempted, False = attempted and
        # failed (use bitmap fallback), TTF_Font = ready.
        self._font_state = None
        self._text_cache: dict = {}
        # Right-side text is special: changes every second when fed an
        # uptime string, so we keep a single live surface and only
        # re-render when the text changes (avoids unbounded cache growth).
        self._right_cache_text: str = ""
        self._right_cache_surface = None

    # ── Text rendering primitives (TTF preferred, bitmap fallback) ────

    def _ensure_font(self):
        """Lazy-load a default TTF font on first use. Returns the font
        handle on success, False if SDL_ttf isn't usable. Memoised."""
        if self._font_state is not None:
            return self._font_state
        try:
            from kernel.gui.sdl2.sdlttf import TTF_Init, TTF_OpenDefaultFont
            TTF_Init()
            self._font_state = TTF_OpenDefaultFont(TTF_FONT_PT)
        except Exception as e:
            log.info(f"menubar: SDL_ttf unavailable ({e}); using bitmap font")
            self._font_state = False
        return self._font_state

    def _text_surface(self, text: str, color: int):
        """Return a cached host-backed surface holding ``text`` rendered
        in ``color``. The surface lives for the life of the menu bar
        (small set: titles + dropdown items × a handful of palette
        entries). Returns None on TTF failure."""
        font = self._ensure_font()
        if not font:
            return None
        key = (text, color)
        s = self._text_cache.get(key)
        if s is not None:
            return s
        try:
            from kernel.gui.sdl2.sdlttf import TTF_RenderUTF8_Blended
            # Render as RGBA: 0xRRGGBBAA (full alpha).
            rgba = ((color & 0xFFFFFF) << 8) | 0xFF
            s = TTF_RenderUTF8_Blended(font, text, rgba)
        except Exception as e:
            log.warn(f"menubar: TTF_RenderUTF8_Blended({text!r}): {e}")
            return None
        self._text_cache[key] = s
        return s

    def _measure_text(self, text: str) -> tuple[int, int]:
        """Return ``(w, h)`` of ``text`` as it will render. Uses the
        already-cached fg-colored surface for TTF mode, or fixed-pitch
        bitmap math for the fallback."""
        if self._ensure_font():
            ts = self._text_surface(text, MENU_BAR_FG)
            if ts is not None:
                return (ts.w, ts.h)
        # Bitmap fallback.
        return (max(1, len(text)) * GLYPH_W, GLYPH_H)

    def _draw_text(self, surface, x: int, y: int,
                   text: str, color: int, bg: int) -> tuple[int, int]:
        """Draw ``text`` at ``(x, y)`` on ``surface`` in ``color``.
        Returns ``(width, height)`` of the rendered glyph run. ``bg``
        is only honoured by the bitmap fallback — TTF surfaces blend
        with whatever is already in the destination."""
        if self._ensure_font():
            ts = self._text_surface(text, color)
            if ts is not None:
                from kernel.gui.sdl2.surface import SDL_BlitSurface, SDL_Rect
                SDL_BlitSurface(ts, None, surface, SDL_Rect(x, y, ts.w, ts.h))
                return (ts.w, ts.h)
        surface.draw_text(x, y, text, fg=color, bg=bg)
        return (max(1, len(text)) * GLYPH_W, GLYPH_H)

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
        and either ``SDL_BlitSurface`` (TTF path) or ``draw_text`` (bitmap
        fallback). Returns the height of the bar so the compositor knows
        how much vertical space it consumes."""
        # Bar background.
        surface._fill_rect(0, 0, total_w, MENU_BAR_H, MENU_BAR_BG)

        # Lay out titles left to right using real text widths.
        self._title_rects = []
        x = MENU_TITLE_GAP
        menus = self.all_menus()
        for i, m in enumerate(menus):
            tw, th = self._measure_text(m.title)
            box_w = tw + MENU_TITLE_PAD_X
            box_x = x - MENU_TITLE_PAD_X // 2
            if i == self._open_idx:
                surface._fill_rect(box_x, 0, box_w, MENU_BAR_H, MENU_BAR_HOT_BG)
                fg, bg = MENU_BAR_HOT_FG, MENU_BAR_HOT_BG
            else:
                fg, bg = MENU_BAR_FG, MENU_BAR_BG
            text_y = max(0, (MENU_BAR_H - th) // 2)
            self._draw_text(surface, x, text_y, m.title, fg, bg)
            self._title_rects.append((box_x, 0, box_w, MENU_BAR_H))
            x += box_w + MENU_TITLE_GAP

        # Right-aligned text (clock / uptime / status). Cached so the
        # per-second uptime tick only allocates when the value changes.
        self._render_right_text(surface, total_w)

        # Render the open dropdown last so it sits on top of nothing
        # (windows are drawn after the menu bar in the compositor).
        self._dropdown_anchor = None
        self._item_rects = []
        if self._open_idx >= 0 and self._open_idx < len(menus):
            self._render_dropdown(surface, menus[self._open_idx],
                                   self._title_rects[self._open_idx][0],
                                   MENU_BAR_H, total_w)

        return MENU_BAR_H

    def _render_right_text(self, surface, total_w: int) -> None:
        """Paint the right-aligned text (uptime / status). For the TTF
        path, surfaces are cached and only re-rendered when the string
        changes — so the once-per-second clock tick costs at most one
        TTF render + one host blit, not a per-frame allocation."""
        if not self._right_text:
            self._right_cache_text = ""
            if self._right_cache_surface is not None:
                self._right_cache_surface.free()
                self._right_cache_surface = None
            return

        if self._ensure_font():
            if self._right_text != self._right_cache_text:
                if self._right_cache_surface is not None:
                    self._right_cache_surface.free()
                # Render fresh; do NOT use _text_surface (which caches
                # forever) — uptime would leak a surface per second.
                try:
                    from kernel.gui.sdl2.sdlttf import TTF_RenderUTF8_Blended
                    font = self._ensure_font()
                    rgba = ((MENU_BAR_FG & 0xFFFFFF) << 8) | 0xFF
                    self._right_cache_surface = \
                        TTF_RenderUTF8_Blended(font, self._right_text, rgba)
                    self._right_cache_text = self._right_text
                except Exception:
                    self._right_cache_surface = None
            s = self._right_cache_surface
            if s is not None:
                from kernel.gui.sdl2.surface import SDL_BlitSurface, SDL_Rect
                rx = total_w - s.w - MENU_TITLE_GAP
                ry = max(0, (MENU_BAR_H - s.h) // 2)
                SDL_BlitSurface(s, None, surface, SDL_Rect(rx, ry, s.w, s.h))
                return
        # Bitmap fallback.
        tw = len(self._right_text) * GLYPH_W
        rx = total_w - tw - MENU_TITLE_GAP
        ry = max(0, (MENU_BAR_H - GLYPH_H) // 2)
        surface.draw_text(rx, ry, self._right_text,
                           fg=MENU_BAR_FG, bg=MENU_BAR_BG)

    def _measure_dropdown(self, menu: Menu) -> tuple[int, int]:
        """Return (width, height) for ``menu``'s dropdown panel."""
        text_w = 0
        for it in menu.items:
            if it.separator:
                continue
            tw, _ = self._measure_text(it.label)
            if tw > text_w:
                text_w = tw
        w = text_w + DROPDOWN_PAD_X * 2
        h = DROPDOWN_PAD_Y * 2
        for it in menu.items:
            h += SEPARATOR_H if it.separator else ITEM_H
        return (max(w, 120), h)

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
            _tw, th = self._measure_text(it.label)
            self._draw_text(surface, x + DROPDOWN_PAD_X,
                             cy + (row_h - th) // 2,
                             it.label, fg, bg)
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

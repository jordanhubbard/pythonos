"""
kernel.gui.compositor — Stacking window manager.

v0 surface:
    * One :class:`CompositorWindow` per app, holding an XRGB8888 surface.
    * Front-to-back z-order list; rear-most painted first.
    * Each window optionally gets a 16-pixel title bar.
    * Focus follows the topmost window; Tab/Shift-Tab cycles focus
      (mouse follow-up adds drag + click-to-focus).
    * Async draw task @ 30 fps blits every dirty window to the live
      framebuffer.

Apps register a CompositorWindow via :func:`Compositor.add_window`,
then either render directly into ``window.surface`` or, for SDL2
compatibility, point an ``sdl2.SDL_Window`` at the compositor window.
"""

import asyncio
import kernel.log as log
from kernel.display import framebuffer as _fb_mod
from kernel.display.font import GLYPH_W, GLYPH_H
from kernel.gui import input as _gui_input
from kernel.gui.dock import (
    Dock,
    Popup,
    desktop_background_hit,
    desktop_popup_items,
    dock_popup_items,
    is_context_click,
)
from kernel.gui.sdl2.surface import SDL_Surface


# ── Title-bar + dock geometry ──────────────────────────────────────────────

TITLE_BAR_H = 22     # tall enough for 11pt TTF + breathing room
CHROME_BORDER = 1
CHROME_FOCUS_BG   = 0x224488
CHROME_UNFOCUS_BG = 0x303030
CHROME_FG         = 0xFFFFFF
CLOSE_BOX_W       = 14
CLOSE_BOX_PAD     = 4
CLOSE_BG          = 0xC04040
CLOSE_BG_HOT      = 0xE05050
CLOSE_FG          = 0xFFFFFF

DOCK_H            = 72
DOCK_BG           = 0x14182A
DOCK_PAD          = 8
DOCK_ICON_SIZE    = 48
DOCK_ICON_GAP     = 12
DOCK_ICON_HOT_BG  = 0x4860A0
DOCK_FG           = 0xFFFFFF
DOCK_LABEL_BG     = 0x000000
DOCK_LABEL_FG     = 0xFFFFFF

# The dropdown menu bar lives in kernel.gui.menubar and owns its own
# geometry constants (MENU_BAR_H, palette, padding). The compositor
# only invokes menubar.render() — no constants needed at this layer.


# ── CompositorWindow ────────────────────────────────────────────────────────

class CompositorWindow:
    """One displayable window. Apps mutate ``surface`` then mark
    ``dirty = True`` to schedule a redraw."""

    def __init__(self, title: str, x: int, y: int, w: int, h: int,
                 chrome: bool = True) -> None:
        self.title  = title
        self.x      = x
        self.y      = y
        self.w      = w
        self.h      = h
        self.chrome = chrome
        # Chipset Workbench compose blits guest pixels onto playfields.
        # Host-backed surfaces have pixels=None and would paint empty bodies.
        host_backed = None
        try:
            from kernel.chipset import chipset as _cs
            if _cs.is_running:
                host_backed = False
        except Exception:
            host_backed = None
        self.surface = SDL_Surface(w, h, host_backed=host_backed)
        self.dirty   = True
        self.focused = False
        self._on_event = None  # callback fn(Event) — set by app
        self._closed   = False
        # Menubar binding: which registered app this window belongs to.
        # Filled in by Compositor.add_window() when the dock launcher
        # has set _launching_app — lets focus changes pull the app's
        # declared menus from the registry without each app having to
        # call set_window_menus() itself.
        self.app_name: str = ""
        # Per-window menus. When non-empty, the menubar shows these on
        # focus instead of the registry's static menus — apps that want
        # actions bound to *this specific window's* state (e.g. editor's
        # File > Save acting on this open file) populate this in their
        # main() function. Empty falls back to registry.get(app_name).menus.
        self.menus: list = []

    def set_event_handler(self, fn) -> None:
        self._on_event = fn

    def deliver(self, ev) -> None:
        if self._on_event:
            try:
                self._on_event(ev)
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True


# ── Compositor ──────────────────────────────────────────────────────────────

class Compositor:
    """Singleton; one per system. Owns the input-routing task and the
    redraw task. v0 has no mouse so window placement is set by apps."""

    def __init__(self) -> None:
        self._windows: list[CompositorWindow] = []
        self._focus_idx = -1
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._tick_hz = 30
        self._desktop_bg = 0x202840   # deep-navy desktop
        # Mouse-drag state
        self._drag_win: CompositorWindow | None = None
        self._drag_off_x = 0
        self._drag_off_y = 0
        # Off-screen back buffer (kernel.display.Surface). Lazy-allocated
        # the first time we have a framebuffer to size against. Painting
        # a full frame to this and then presenting in one bulk MMIO write
        # eliminates the clear→paint flicker.
        self._back: 'Surface | None' = None
        # Bridge presenter: when the host pythonos_bridge companion is
        # reachable, push draw commands directly to it instead of
        # composing in-guest. Set lazily in start().
        self._bridge_present = False
        self._bridge_needs_redraw = True
        self._bridge_last_uptime = ""
        self._bridge_w = 0
        self._bridge_h = 0
        self._bridge_fb_handle = 0    # window's main surface handle
        # App dock — pinned bundled apps plus running unpinned transients.
        self._dock = Dock()
        self._popup = Popup()
        self._mods = 0                 # tracked Ctrl for control-click
        self._active_launches: set[str] = set()
        self._dock_hot = -1            # currently-hovered slot index or -1
        self._dock_icons: dict[str, object] = {}    # name → SDL_Surface
        self._close_hot_win: 'CompositorWindow | None' = None
        # Pre-uploaded desktop background; lazy-loaded the first time
        # bridge mode opens. Decoded from kernel.gui.assets.DESKTOP_BG_PNG.
        self._bg_surface: 'SDL_Surface | None' = None
        self._boot_tick0 = 0   # snapshot at start() — used for menu-bar uptime
        # macOS-style menu bar at the top. The system menus are seeded
        # once by py_desktop(); per-app menus follow focus changes. The
        # bar's render() owns drawing the wordmark + clock area too.
        from kernel.gui.menubar import MenuBar
        self._menubar = MenuBar()
        # Set by _launch_dock_app for the duration of an app's startup
        # so add_window() can stamp the new window with the app's name.
        self._launching_app: str = ""

    # ── Window registry ─────────────────────────────────────────────────────

    @property
    def _dock_apps(self) -> list:
        """Visible dock slots as (name, entry, icon_factory) tuples."""
        return [(i.name, i.entry, i.icon_factory) for i in self._dock.visible()]

    def add_window(self, win: CompositorWindow) -> None:
        if not win.app_name and self._launching_app:
            win.app_name = self._launching_app
        old = self.focused_window
        if old is not None:
            old.focused = False
            old.dirty = True
        self._windows.append(win)
        self._focus_idx = len(self._windows) - 1
        win.focused = True
        self._refresh_app_menus(win)
        if win.app_name:
            self._refresh_dock_running(win.app_name)
        self._bridge_needs_redraw = True
        win.dirty = True

    def remove_window(self, win: CompositorWindow) -> None:
        if win not in self._windows:
            return
        idx = self._windows.index(win)
        name = win.app_name
        self._windows.remove(win)
        if idx == self._focus_idx and self._windows:
            self._focus_idx = min(self._focus_idx, len(self._windows) - 1)
            self._windows[self._focus_idx].focused = True
            self._refresh_app_menus(self._windows[self._focus_idx])
        elif not self._windows:
            self._focus_idx = -1
            self._refresh_app_menus(None)
        if name:
            self._refresh_dock_running(name)
        self._bridge_needs_redraw = True

    def cycle_focus(self, direction: int = 1) -> None:
        if not self._windows:
            return
        if 0 <= self._focus_idx < len(self._windows):
            self._windows[self._focus_idx].focused = False
        self._focus_idx = (self._focus_idx + direction) % len(self._windows)
        self._windows[self._focus_idx].focused = True
        self._refresh_app_menus(self._windows[self._focus_idx])
        self._bridge_needs_redraw = True
        for w in self._windows:
            w.dirty = True

    def _refresh_app_menus(self, win) -> None:
        """Replace the menubar's app-menu list with the focused window's
        menus.

        Resolution order:
          1. ``win.menus`` if non-empty (per-window dynamic menus, e.g.
             editor's File menu bound to this open file).
          2. ``registry.get(win.app_name).menus`` (static per-app menus
             declared at registration time).
          3. ``[]`` (no app menus — the system menu still shows).
        """
        menus: list = []
        if win is not None:
            if win.menus:
                menus = list(win.menus)
            elif win.app_name:
                try:
                    from apps import registry as _reg
                    info = _reg.get(win.app_name)
                    if info is not None:
                        menus = list(info.menus or [])
                except Exception:
                    menus = []
        self._menubar.set_app_menus(menus)

    @property
    def focused_window(self) -> CompositorWindow | None:
        if 0 <= self._focus_idx < len(self._windows):
            return self._windows[self._focus_idx]
        return None

    # ── Drawing ─────────────────────────────────────────────────────────────

    def _paint_chrome(self, win: CompositorWindow, fb) -> None:
        if not win.chrome:
            return
        bg = CHROME_FOCUS_BG if win.focused else CHROME_UNFOCUS_BG
        fb.fill_rect(win.x, win.y, win.w, TITLE_BAR_H, bg)
        title = win.title or ""
        if len(title) * GLYPH_W > win.w - 8:
            title = title[: max(1, (win.w - 8) // GLYPH_W)]
        fb.draw_text(win.x + 4, win.y + (TITLE_BAR_H - GLYPH_H) // 2,
                     title, fg=CHROME_FG, bg=bg)

    def _paint_window_body(self, win: CompositorWindow, fb) -> None:
        body_y = win.y + (TITLE_BAR_H if win.chrome else 0)
        s = win.surface
        fb.blit_buffer(s.pixels, s.w, s.h, win.x, body_y)

    def _chipset_running(self) -> bool:
        try:
            from kernel.chipset import chipset as _cs
            return _cs.is_running
        except Exception:
            return False

    async def _redraw(self) -> None:
        if self._chipset_running():
            uptime = self._uptime_str()
            if (self._back is not None
                    and uptime == self._bridge_last_uptime
                    and not self._bridge_needs_redraw
                    and not any(w.dirty for w in self._windows)
                    and not self._menubar.is_open):
                return
            self._bridge_last_uptime = uptime
            self._bridge_needs_redraw = False
            self._redraw_local()
            return
        if self._bridge_present:
            uptime = self._uptime_str()
            if (not self._bridge_needs_redraw
                    and uptime == self._bridge_last_uptime
                    and not any(w.dirty for w in self._windows)):
                return
            self._bridge_last_uptime = uptime
            self._bridge_needs_redraw = False
            self._redraw_bridge(uptime)
            for w in self._windows:
                w.dirty = False
        else:
            if not any(w.dirty for w in self._windows):
                return
            self._redraw_local()

    def _redraw_bridge(self, uptime_text: str | None = None) -> None:
        """Issue draw commands straight to the host SDL window. No
        guest back-buffer; per-frame data on the wire is just JSON
        envelopes (no pixel payloads)."""
        from kernel.bridge import bridge as _br, BridgeError
        from kernel.gui.sdl2.surface import SDL_Surface
        from kernel.gui.text import text_renderer
        fb_handle = self._bridge_fb_handle
        fb_surf = SDL_Surface.from_handle(fb_handle,
                                            self._bridge_w, self._bridge_h)
        try:
            # Desktop background — blit the pre-uploaded image if loaded,
            # otherwise fall back to a solid-colour fill.
            bg = self._bg_surface
            if bg is not None:
                src_handle = bg._sync_to_host()
                if src_handle != 0:
                    _br.cast("surface.blit", {
                        "src": src_handle,
                        "dst": fb_handle,
                        "dst_rect": {"x": 0, "y": 0, "w": bg.w, "h": bg.h},
                    })
                else:
                    _br.cast("surface.fill_rect", {
                        "handle": fb_handle, "rect": None,
                        "rgb": (self._desktop_bg & 0xFFFFFF) | 0xFF000000,
                    })
            else:
                _br.cast("surface.fill_rect", {
                    "handle": fb_handle, "rect": None,
                    "rgb": (self._desktop_bg & 0xFFFFFF) | 0xFF000000,
                })
            for win in self._windows:
                if win.chrome:
                    chrome_color = CHROME_FOCUS_BG if win.focused else CHROME_UNFOCUS_BG
                    _br.cast("surface.fill_rect", {
                        "handle": fb_handle,
                        "rect": {"x": win.x, "y": win.y,
                                  "w": win.w, "h": TITLE_BAR_H},
                        "rgb": (chrome_color & 0xFFFFFF) | 0xFF000000,
                    })
                    # Title text — TTF, truncated to fit the title bar.
                    title_text = win.title or ""
                    title_max_w = max(1, win.w - 8 - CLOSE_BOX_W - 4)
                    title_text = text_renderer.truncate_to_width(
                        title_text, title_max_w, size=11)
                    _tw, th = text_renderer.measure(title_text, size=11)
                    text_renderer.draw(fb_surf,
                                        win.x + 4,
                                        win.y + (TITLE_BAR_H - th) // 2,
                                        title_text, CHROME_FG, size=11)
                    # Close box (top-right of chrome).
                    cx, cy, cw, ch = self._close_box_rect(win)
                    is_hot = (self._close_hot_win is win)
                    _br.cast("surface.fill_rect", {
                        "handle": fb_handle,
                        "rect": {"x": cx, "y": cy, "w": cw, "h": ch},
                        "rgb": ((CLOSE_BG_HOT if is_hot else CLOSE_BG)
                                & 0xFFFFFF) | 0xFF000000,
                    })
                    # Centered ×. Lowercase 'x' renders cleanly in both
                    # TTF (which has the multiplication sign too, but
                    # this is consistent with the older look) and the
                    # bitmap-font fallback (which doesn't carry U+00D7).
                    xtw, xth = text_renderer.measure("x", size=11)
                    text_renderer.draw(fb_surf,
                                        cx + (cw - xtw) // 2,
                                        cy + (ch - xth) // 2,
                                        "x", CLOSE_FG, size=11)
                # Window body — blit src surface to dst at body position.
                # _sync_to_host handles host-backed (no-op), guest-backed
                # (lazy-create + upload), and mirrored (re-upload if dirty).
                s = win.surface
                src_handle = s._sync_to_host()
                if src_handle != 0:
                    body_y = win.y + (TITLE_BAR_H if win.chrome else 0)
                    _br.cast("surface.blit", {
                        "src": src_handle,
                        "dst": fb_handle,
                        "dst_rect": {"x": win.x, "y": body_y,
                                      "w": s.w, "h": s.h},
                    })
            self._draw_dock_bridge(fb_handle, fb_surf)
            # Menu bar last so any open dropdown sits on top of the
            # rest of the desktop. Refresh the right-side uptime text
            # each frame so the clock ticks visibly. Caller may pass an
            # uptime override (testing); otherwise we sample _hal ticks.
            self._menubar.set_right_text(uptime_text or self._uptime_str())
            self._menubar.render(fb_surf, self._bridge_w)
            self._menubar.paint_popup(fb_surf, self._popup)
            _br.call("display.present", {})
        except BridgeError as e:
            log.warn(f"compositor: bridge frame failed ({e}); "
                     f"falling back to local framebuffer")
            self._bridge_present = False
            self._redraw_local()

    def _uptime_str(self) -> str:
        """HH:MM:SS since compositor.start(). Wall-clock time would
        need an RTC; kernel doesn't have one yet, so uptime is what we
        can honestly show."""
        try:
            import _hal
            ticks = int(getattr(_hal, "_pit_ticks", 0) or 0)
        except Exception:
            ticks = 0
        # PIT/timer is 100 Hz on both archs; each tick = 10 ms.
        elapsed = max(0, (ticks - self._boot_tick0) // 100)
        h = elapsed // 3600
        m = (elapsed // 60) % 60
        s = elapsed % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _dock_total_w(self) -> int:
        n = len(self._dock_apps)
        if n == 0:
            return 0
        return n * DOCK_ICON_SIZE + (n - 1) * DOCK_ICON_GAP

    def _desktop_size(self) -> tuple[int, int]:
        if self._bridge_w and self._bridge_h:
            return self._bridge_w, self._bridge_h
        fb = _fb_mod.fb
        if fb is not None:
            return fb.width, fb.height
        return 1024, 768

    def _dock_first_x(self) -> int:
        return (self._desktop_size()[0] - self._dock_total_w()) // 2

    def _draw_dock_bridge(self, fb_handle: int, fb_surf=None) -> None:
        """Paint the app dock at the bottom of the desktop window —
        macOS-flavored: centered, square icons (not text labels), with
        a tooltip-style label above the hovered slot."""
        if not self._dock_apps:
            return
        from kernel.bridge import bridge as _br
        from kernel.gui.text import text_renderer
        if fb_surf is None:
            from kernel.gui.sdl2.surface import SDL_Surface
            fb_surf = SDL_Surface.from_handle(fb_handle,
                                                self._bridge_w, self._bridge_h)
        dock_y = self._bridge_h - DOCK_H
        # Dock backdrop.
        _br.cast("surface.fill_rect", {
            "handle": fb_handle,
            "rect": {"x": 0, "y": dock_y,
                      "w": self._bridge_w, "h": DOCK_H},
            "rgb": (DOCK_BG & 0xFFFFFF) | 0xFF000000,
        })
        first_x = self._dock_first_x()
        slot_y  = dock_y + (DOCK_H - DOCK_ICON_SIZE) // 2
        for i, entry_tuple in enumerate(self._dock_apps):
            name = entry_tuple[0]
            slot_x = first_x + i * (DOCK_ICON_SIZE + DOCK_ICON_GAP)
            if i == self._dock_hot:
                _br.cast("surface.fill_rect", {
                    "handle": fb_handle,
                    "rect": {"x": slot_x - 4, "y": slot_y - 4,
                              "w": DOCK_ICON_SIZE + 8,
                              "h": DOCK_ICON_SIZE + 8},
                    "rgb": (DOCK_ICON_HOT_BG & 0xFFFFFF) | 0xFF000000,
                })
            icon = self._ensure_icon(name)
            if icon is not None:
                src_handle = icon._sync_to_host()
                if src_handle != 0:
                    _br.cast("surface.blit", {
                        "src": src_handle,
                        "dst": fb_handle,
                        "dst_rect": {"x": slot_x, "y": slot_y,
                                      "w": icon.w, "h": icon.h},
                    })
        if self._dock_hot >= 0:
            label = self._dock_apps[self._dock_hot][0]
            tw, th = text_renderer.measure(label, size=11)
            label_w = tw + 12
            label_h = th + 6
            label_x = (first_x
                        + self._dock_hot * (DOCK_ICON_SIZE + DOCK_ICON_GAP)
                        + (DOCK_ICON_SIZE - label_w) // 2)
            label_y = dock_y - label_h - 4
            label_x = max(4, min(self._bridge_w - 4 - label_w, label_x))
            _br.cast("surface.fill_rect", {
                "handle": fb_handle,
                "rect": {"x": label_x, "y": label_y,
                          "w": label_w, "h": label_h},
                "rgb": (DOCK_LABEL_BG & 0xFFFFFF) | 0xFF000000,
            })
            text_renderer.draw(fb_surf,
                                label_x + 6, label_y + 3,
                                label, DOCK_LABEL_FG, size=11)

    def _ensure_icon(self, name: str):
        cached = self._dock_icons.get(name)
        if cached is not None:
            return cached
        for entry_tuple in self._dock_apps:
            if entry_tuple[0] != name:
                continue
            factory = entry_tuple[2]
            try:
                if factory is not None:
                    surf = factory()
                else:
                    from apps._icons import default_icon
                    surf = default_icon(name)
            except Exception as e:
                log.warn(f"dock icon factory for {name}: {e}")
                return None
            self._dock_icons[name] = surf
            return surf
        return None

    def _close_box_rect(self, win: CompositorWindow) -> tuple[int, int, int, int]:
        """Returns (x, y, w, h) of the close box for `win`'s chrome."""
        return (
            win.x + win.w - CLOSE_BOX_W - CLOSE_BOX_PAD,
            win.y + (TITLE_BAR_H - CLOSE_BOX_W) // 2,
            CLOSE_BOX_W, CLOSE_BOX_W,
        )

    def _close_box_hit(self, win: CompositorWindow,
                        x: int, y: int) -> bool:
        if not win.chrome:
            return False
        cx, cy, cw, ch = self._close_box_rect(win)
        return cx <= x < cx + cw and cy <= y < cy + ch

    def _dock_slot_at(self, x: int, y: int) -> int:
        if not self._dock_apps:
            return -1
        _, desk_h = self._desktop_size()
        dock_y = desk_h - DOCK_H
        if not (dock_y <= y < desk_h):
            return -1
        first_x = self._dock_first_x()
        for i in range(len(self._dock_apps)):
            slot_x = first_x + i * (DOCK_ICON_SIZE + DOCK_ICON_GAP)
            if slot_x <= x < slot_x + DOCK_ICON_SIZE:
                return i
        return -1

    def register_dock_app(self, name: str, entry,
                            icon_factory=None) -> None:
        """Pin an app to the dock. `entry` is an awaitable callable;
        `icon_factory` is an optional zero-arg function that returns
        a 48x48 SDL_Surface."""
        self._dock.pin(name, entry, icon_factory)

    def _refresh_dock_running(self, name: str) -> None:
        launching = name in self._active_launches
        has_win = any(w.app_name == name for w in self._windows)
        self._dock.set_running(name, launching or has_win)
        n = len(self._dock_apps)
        if self._dock_hot >= n:
            self._dock_hot = -1
        self._bridge_needs_redraw = True

    def _window_rects(self) -> list:
        rects = []
        for win in self._windows:
            h = win.h + (TITLE_BAR_H if win.chrome else 0)
            rects.append((win.x, win.y, win.w, h))
        return rects

    def _mark_chrome_dirty(self) -> None:
        if self._windows:
            self._windows[-1].dirty = True
        self._bridge_needs_redraw = True

    async def _launch_dock_app(self, name: str, entry) -> None:
        # Stamp newly-created windows with the app name so the menu bar
        # can pick up registered per-app menus on focus.
        prev = self._launching_app
        self._launching_app = name
        self._active_launches.add(name)
        self._refresh_dock_running(name)
        try:
            await entry()
        except Exception as e:
            log.warn(f"dock: {name} crashed: {e}")
        finally:
            self._launching_app = prev
            self._active_launches.discard(name)
            self._refresh_dock_running(name)

    def launch_app(self, name: str, *args) -> None:
        """Spawn an app by registry name. Apps inside the desktop call
        this to open another app (e.g. files browser → editor). Unpinned
        apps appear in the dock while they run."""
        from apps import registry
        info = registry.get(name)
        if info is None:
            log.warn(f"launch_app: no such app '{name}'")
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._dock.ensure(name, info.entry, info.icon_factory)
        loop.create_task(self._launch_dock_app(name,
                                                lambda: info.entry(*args)))

    def _paint_dock_local(self, back) -> None:
        """Bitmap-font dock onto the in-guest back buffer (chipset path)."""
        if not self._dock_apps:
            return
        w, h = back.width, back.height
        dock_y = h - DOCK_H
        back.fill_rect(0, dock_y, w, DOCK_H, DOCK_BG)
        first_x = (w - self._dock_total_w()) // 2
        slot_y = dock_y + (DOCK_H - DOCK_ICON_SIZE) // 2
        for i, entry_tuple in enumerate(self._dock_apps):
            name = entry_tuple[0]
            slot_x = first_x + i * (DOCK_ICON_SIZE + DOCK_ICON_GAP)
            if i == self._dock_hot:
                back.fill_rect(slot_x - 4, slot_y - 4,
                               DOCK_ICON_SIZE + 8, DOCK_ICON_SIZE + 8,
                               DOCK_ICON_HOT_BG)
            icon = self._ensure_icon(name)
            pix = getattr(icon, "pixels", None) if icon is not None else None
            if pix is not None:
                back.blit_buffer(pix, icon.w, icon.h, slot_x, slot_y)
            else:
                back.fill_rect(slot_x, slot_y, DOCK_ICON_SIZE, DOCK_ICON_SIZE,
                               0x303040)
                if name:
                    back.draw_text(slot_x + 16, slot_y + 20, name[0].upper(),
                                   fg=DOCK_FG, bg=0x303040)
        if self._dock_hot < 0:
            return
        label = self._dock_apps[self._dock_hot][0]
        tw = len(label) * GLYPH_W
        label_w = tw + 12
        label_h = GLYPH_H + 6
        label_x = (first_x
                   + self._dock_hot * (DOCK_ICON_SIZE + DOCK_ICON_GAP)
                   + (DOCK_ICON_SIZE - label_w) // 2)
        label_y = dock_y - label_h - 4
        label_x = max(4, min(w - 4 - label_w, label_x))
        back.fill_rect(label_x, label_y, label_w, label_h, DOCK_LABEL_BG)
        back.draw_text(label_x + 6, label_y + 3, label,
                       fg=DOCK_LABEL_FG, bg=DOCK_LABEL_BG)

    def _redraw_local(self) -> None:
        """In-guest compose. Presents via framebuffer unless the chipset
        clock owns the scan — then the composed back-buffer is copied
        into the Workbench playfield and the chipset presents."""
        chipset_mod = None
        try:
            from kernel.chipset import chipset as chipset_mod
            if chipset_mod.is_running and chipset_mod.active_view is not chipset_mod.workbench:
                return
        except Exception:
            chipset_mod = None

        fb = _fb_mod.fb
        if fb == None:
            return
        if self._back is None or \
                self._back.width != fb.width or self._back.height != fb.height:
            from kernel.display.framebuffer import Surface
            self._back = Surface(fb.width, fb.height)
        back = self._back
        if self._bg_surface is None:
            self._load_desktop_bg()
        bg = self._bg_surface
        bg_pix = getattr(bg, "pixels", None) if bg is not None else None
        if bg_pix is not None:
            back.fill(self._desktop_bg)
            back.blit_buffer(bg_pix, bg.w, bg.h, 0, 0)
        else:
            back.fill(self._desktop_bg)
        for win in self._windows:
            self._paint_chrome(win, back)
            s = win.surface
            pixels = getattr(s, "pixels", None)
            if pixels is not None:
                back.blit_buffer(pixels, s.w, s.h, win.x,
                                 win.y + (TITLE_BAR_H if win.chrome else 0))
            win.dirty = False
        self._paint_dock_local(back)
        font_state = self._menubar._font_state
        self._menubar._font_state = False
        try:
            self._menubar.set_right_text(self._uptime_str())
            self._menubar.render(back, back.width)
            self._menubar.paint_popup(back, self._popup)
        finally:
            self._menubar._font_state = font_state
        if chipset_mod is not None and chipset_mod.is_running:
            wb = chipset_mod.workbench
            if wb is not None and chipset_mod.active_view is wb:
                if len(back._buf) == len(wb.pf0.pixels):
                    wb.pf0.pixels[:] = back._buf
                    # A compositor redraw and the chipset clock are separate
                    # asyncio tasks.  Present this completed Workbench frame
                    # now instead of requiring a later chipset tick; under
                    # slow/TCG guests both tasks can otherwise go to sleep
                    # after their first pass with the old (blank) scanout.
                    fb.present(back._buf)
            return
        fb.present(back._buf)

    # ── Hit-testing & focus ─────────────────────────────────────────────────

    def _window_at(self, x: int, y: int) -> CompositorWindow | None:
        """Topmost window covering (x, y), including its chrome."""
        # We paint front-to-back as list order, so the LAST-painted window
        # is on top. Iterate in reverse for a topmost-first hit test.
        for win in reversed(self._windows):
            top = win.y
            bottom = win.y + (TITLE_BAR_H if win.chrome else 0) + win.h
            right  = win.x + win.w
            if win.x <= x < right and top <= y < bottom:
                return win
        return None

    def _focus(self, win: CompositorWindow) -> None:
        if win not in self._windows:
            return
        old = self.focused_window
        if old is win:
            return
        if old != None:
            old.focused = False
            old.dirty = True
        self._focus_idx = self._windows.index(win)
        win.focused = True
        win.dirty = True
        self._refresh_app_menus(win)
        # Raise to top of stack so it paints last (and registers as topmost
        # in subsequent hit-tests).
        self._windows.remove(win)
        self._windows.append(win)
        self._focus_idx = len(self._windows) - 1
        self._bridge_needs_redraw = True

    # ── Event routing ───────────────────────────────────────────────────────

    def _handle_context_click(self, x: int, y: int) -> bool:
        """Open a dock-icon or desktop launch menu. Returns True if a
        menu was shown (the click is consumed)."""
        self._menubar.close()
        desk_w, desk_h = self._desktop_size()
        slot = self._dock_slot_at(x, y)
        if slot >= 0:
            name = self._dock_apps[slot][0]
            pinned = self._dock.is_pinned(name)

            def keep(n=name) -> None:
                self._dock.pin(n)
                self._mark_chrome_dirty()

            def remove(n=name) -> None:
                self._dock.unpin(n)
                nvis = len(self._dock_apps)
                if self._dock_hot >= nvis:
                    self._dock_hot = -1
                self._mark_chrome_dirty()

            items = dock_popup_items(pinned, on_keep=keep, on_remove=remove)
            self._popup.show(x, y, items, desk_w)
            self._mark_chrome_dirty()
            return True
        if desktop_background_hit(x, y, desk_w, desk_h,
                                  window_rects=self._window_rects()):
            from apps import registry
            items = desktop_popup_items(registry.list_apps(),
                                        launch=self.launch_app)
            self._popup.show(x, y, items, desk_w)
            self._mark_chrome_dirty()
            return True
        return False

    def _route_event(self, ev) -> None:
        # Tab / Shift-Tab cycles focus globally
        if ev.kind == _gui_input.KEY_DOWN and ev.code == _gui_input.KEY_TAB:
            direction = -1 if (ev.mods & _gui_input.MOD_SHIFT) else 1
            self.cycle_focus(direction)
            return

        # Track Ctrl so control-click works even if mouse events omit mods.
        # Event-kind KEY_DOWN/KEY_UP names are overwritten by arrow codes
        # later in kernel.gui.input (1 / 2 remain the kind values).
        if ev.kind in (1, 2) and ev.code in (_gui_input.KEY_LCTRL,
                                              _gui_input.KEY_RCTRL):
            if ev.kind == 1:
                self._mods |= _gui_input.MOD_CTRL
            else:
                self._mods &= ~_gui_input.MOD_CTRL

        try:
            from kernel.chipset import chipset as _cs
            if (_cs.active_view is not None
                    and _cs.workbench is not None
                    and _cs.active_view is not _cs.workbench):
                cb = getattr(_cs, "on_event", None)
                if cb is not None:
                    cb(ev)
                return
        except Exception:
            pass

        if ev.kind == _gui_input.MOUSE_MOVE and self._popup.is_open:
            if self._popup.on_move(ev.x, ev.y):
                self._mark_chrome_dirty()
            return
        if ev.kind == _gui_input.MOUSE_DOWN and self._popup.is_open:
            self._popup.click(ev.x, ev.y)
            self._mark_chrome_dirty()
            return

        # Menu bar gets first crack at clicks (and at moves while a
        # dropdown is open, so the hover highlight tracks the cursor).
        if ev.kind == _gui_input.MOUSE_MOVE and self._menubar.is_open:
            if self._menubar.on_mouse_move(ev.x, ev.y):
                self._mark_chrome_dirty()
                return
        if ev.kind == _gui_input.MOUSE_DOWN and ev.code == 1:
            if self._menubar.on_mouse_down(ev.x, ev.y):
                self._popup.hide()
                self._mark_chrome_dirty()
                return

        if ev.kind == _gui_input.MOUSE_DOWN:
            mods = ev.mods | self._mods
            if is_context_click(ev.code, mods):
                if self._handle_context_click(ev.x, ev.y):
                    return

        # Mouse-button-down: dock click → focus → maybe-start-drag / close
        if ev.kind == _gui_input.MOUSE_DOWN and ev.code == 1:  # left button
            slot = self._dock_slot_at(ev.x, ev.y)
            if slot >= 0:
                name, entry, _icon = self._dock_apps[slot]
                if entry is None:
                    return
                log.info(f"dock: launching {name}")
                asyncio.get_event_loop().create_task(
                    self._launch_dock_app(name, entry))
                return
            win = self._window_at(ev.x, ev.y)
            if win != None:
                # Close box?
                if self._close_box_hit(win, ev.x, ev.y):
                    log.info(f"window: closing '{win.title}'")
                    win.close()
                    self.remove_window(win)
                    return
                self._focus(win)
                if win.chrome and ev.y < win.y + TITLE_BAR_H:
                    # Click on title bar — start drag
                    self._drag_win  = win
                    self._drag_off_x = ev.x - win.x
                    self._drag_off_y = ev.y - win.y
                else:
                    # Click in body — deliver to the window
                    win.deliver(ev)
            return

        # Mouse-move: continue any in-progress drag, else deliver to focus
        if ev.kind == _gui_input.MOUSE_MOVE:
            if self._drag_win != None:
                self._drag_win.x = ev.x - self._drag_off_x
                self._drag_win.y = ev.y - self._drag_off_y
                # Mark every visible surface dirty so the trail clears
                for w in self._windows:
                    w.dirty = True
                return
            # Dock hover (light visual feedback)
            new_hot = self._dock_slot_at(ev.x, ev.y)
            if new_hot != self._dock_hot:
                self._dock_hot = new_hot
                if self._windows:
                    self._windows[-1].dirty = True   # force redraw
                self._bridge_needs_redraw = True
            win = self.focused_window
            if win != None:
                win.deliver(ev)
            return

        # Mouse-button-up: end drag if any
        if ev.kind == _gui_input.MOUSE_UP and ev.code == 1:
            if self._drag_win != None:
                self._drag_win = None
                return
            win = self.focused_window
            if win != None:
                win.deliver(ev)
            return

        # Everything else (keyboard, other mouse buttons) → focused window
        win = self.focused_window
        if win != None:
            win.deliver(ev)

    # ── Tasks ───────────────────────────────────────────────────────────────

    async def _draw_loop(self) -> None:
        period = 1.0 / self._tick_hz
        while self._running:
            await self._redraw()
            await asyncio.sleep(period)

    async def _input_loop(self) -> None:
        q = _gui_input.queue
        if q == None:
            _gui_input.init()
            q = _gui_input.queue
        while self._running:
            ev = await q.get()
            self._route_event(ev)
            # Reap closed windows lazily
            for w in list(self._windows):
                if w._closed:
                    self.remove_window(w)

    def start(self, loop=None) -> None:
        if self._running:
            return
        self._running = True
        try:
            import _hal
            self._boot_tick0 = int(getattr(_hal, "_pit_ticks", 0) or 0)
        except Exception:
            self._boot_tick0 = 0
        loop = loop or asyncio.get_event_loop()
        self._tasks.append(loop.create_task(self._open_bridge_window()))
        self._tasks.append(loop.create_task(self._draw_loop()))
        self._tasks.append(loop.create_task(self._input_loop()))
        log.info("compositor: started")

    def _load_desktop_bg(self) -> None:
        """Decode the embedded background PNG into an SDL_Surface. Called
        once after bridge mode opens (so we have a host the surface can
        upload to)."""
        if self._bg_surface is not None:
            return
        try:
            from kernel.gui.assets import DESKTOP_BG_PNG
            from kernel.gui.image import load_bytes as _img_load
            self._bg_surface = _img_load(DESKTOP_BG_PNG)
            log.info(f"compositor: desktop background loaded "
                     f"({self._bg_surface.w}x{self._bg_surface.h})")
        except Exception as e:
            log.warn(f"compositor: desktop background load failed: {e}")
            self._bg_surface = None

    async def _open_bridge_window(self) -> None:
        """Probe the host pythonos_bridge with a hello + display.open.
        On success, switch the redraw path to issue draw commands
        directly to the host AND start the input forwarder so SDL
        events from the host window land in kernel.gui.input.queue."""
        from kernel.bridge import bridge as _br, BridgeError
        try:
            _br.hello()
        except Exception as e:
            log.info(f"compositor: bridge unavailable ({e}); using local fb")
            return
        fb = _fb_mod.fb
        cw = fb.width  if fb != None else 1024
        ch = fb.height if fb != None else 768
        try:
            r = _br.call("display.open",
                          {"w": cw, "h": ch, "title": "PythonOS"})
        except BridgeError as e:
            log.warn(f"compositor: display.open failed ({e}); using local fb")
            return
        self._bridge_w = int(r.get("w", cw))
        self._bridge_h = int(r.get("h", ch))
        self._bridge_fb_handle = int(r.get("fb_handle", 0))
        self._bridge_present = True
        self._bridge_needs_redraw = True
        self._bridge_last_uptime = ""
        # Forward host SDL events into kernel.gui.input.queue so the
        # existing _route_event handler picks them up.
        from kernel.bridge import input as _br_input
        _br_input.start_forwarder()
        # Decode + register the desktop background once the host is up.
        self._load_desktop_bg()
        log.info(f"compositor: bridge presenter active "
                 f"({self._bridge_w}x{self._bridge_h}, fb_handle={self._bridge_fb_handle})")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


# Module-level singleton
compositor = Compositor()

"""apps.files.browser — Arrow-key file browser in a compositor window.

Navigation:
    Up / Down       — move selection
    Enter           — descend into a directory (or display file size for files)
    Backspace       — go up one directory
    PgUp / PgDn     — page selection
    ESC             — close

v0 doesn't yet wire the Send / Recv TCP transfer actions — that needs
a small modal-input layer on top of TextWin and is filed as a separate
follow-up if desired.
"""

import asyncio

from kernel.fs.vfs import vfs, InodeType
from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect
from kernel.display.font import GLYPH_W, GLYPH_H
from apps import registry


_BG       = 0x101820
_FG       = 0xCCCCCC
_FG_DIM   = 0x808080
_HL_BG    = 0x355088
_HL_FG    = 0xFFFFFF
_HEADER_H = GLYPH_H + 4


class _Browser:
    def __init__(self, win: CompositorWindow) -> None:
        self.win = win
        self.cwd = "/"
        self.entries: list[tuple[str, str]] = []   # (name, kind)
        self.selected = 0
        self.scroll_top = 0
        self.cols = max(1, win.w // GLYPH_W)
        # Reserve the top row for the header.
        self.list_rows = max(1, (win.h - _HEADER_H) // GLYPH_H)

    async def reload(self) -> None:
        try:
            names = await vfs.readdir(self.cwd)
        except Exception:
            names = []
        rows: list[tuple[str, str]] = []
        for name in sorted(names):
            full = self.cwd.rstrip("/") + "/" + name if self.cwd != "/" else "/" + name
            kind = "?"
            try:
                st = await vfs.stat(full)
                t = getattr(st, "inode_type", None)
                if t == InodeType.DIR:
                    kind = "dir"
                elif t == InodeType.FILE:
                    kind = "file"
                elif t == InodeType.SYMLINK:
                    kind = "link"
            except Exception:
                pass
            rows.append((name, kind))
        if self.cwd != "/":
            rows.insert(0, ("..", "dir"))
        self.entries = rows
        self.selected = 0
        self.scroll_top = 0

    # ── Drawing ─────────────────────────────────────────────────────────

    def _draw_header(self) -> None:
        s = self.win.surface
        s._fill_rect(0, 0, self.win.w, _HEADER_H, _HL_BG)
        s.draw_text(4, 2, ("path: " + self.cwd)[: self.cols], fg=_HL_FG, bg=_HL_BG)

    def _draw_row(self, idx: int, row: int, selected: bool) -> None:
        s = self.win.surface
        y = _HEADER_H + row * GLYPH_H
        bg = _HL_BG if selected else _BG
        fg = _HL_FG if selected else _FG
        s._fill_rect(0, y, self.win.w, GLYPH_H, bg)
        if 0 <= idx < len(self.entries):
            name, kind = self.entries[idx]
            label = name + ("/" if kind == "dir" else "")
            s.draw_text(4, y, label[: self.cols - 8], fg=fg, bg=bg)
            tag = ("  " + kind).rjust(6)
            tx  = self.win.w - len(tag) * GLYPH_W - 4
            tag_fg = _HL_FG if selected else _FG_DIM
            s.draw_text(tx, y, tag, fg=tag_fg, bg=bg)

    def redraw(self) -> None:
        SDL_FillRect(self.win.surface, None, _BG)
        self._draw_header()
        for r in range(self.list_rows):
            idx = self.scroll_top + r
            self._draw_row(idx, r, idx == self.selected)
        self.win.dirty = True

    def _ensure_visible(self) -> None:
        if self.selected < self.scroll_top:
            self.scroll_top = self.selected
        elif self.selected >= self.scroll_top + self.list_rows:
            self.scroll_top = self.selected - self.list_rows + 1

    # ── Actions ─────────────────────────────────────────────────────────

    def _move(self, delta: int) -> None:
        if not self.entries:
            return
        self.selected = max(0, min(len(self.entries) - 1, self.selected + delta))
        self._ensure_visible()

    async def _enter(self) -> None:
        if not self.entries:
            return
        name, kind = self.entries[self.selected]
        if name == "..":
            parts = [p for p in self.cwd.split("/") if p]
            self.cwd = "/" + "/".join(parts[:-1])
            if not self.cwd: self.cwd = "/"
            await self.reload()
            return
        full = self.cwd.rstrip("/") + "/" + name if self.cwd != "/" else "/" + name
        if kind == "dir":
            self.cwd = full
            await self.reload()
            return
        # File — open in the editor app.
        compositor.launch_app("editor", [full])


async def main(*args, **kwargs) -> None:
    win = CompositorWindow("Files", x=80, y=80, w=720, h=500)
    compositor.add_window(win)
    b = _Browser(win)
    await b.reload()
    b.redraw()

    closed = False
    pending = None
    last_click = [0.0, -1]   # [timestamp, row_idx] for double-click detection

    def on_event(ev):
        nonlocal closed, pending
        if ev.kind == _gui_input.MOUSE_DOWN and ev.code == 1:
            # Mouse hits arrive in window-local coords (compositor adjusts for body).
            from kernel.gui.compositor import TITLE_BAR_H
            local_y = ev.y - (win.y + TITLE_BAR_H)
            if local_y < _HEADER_H:
                return
            row = (local_y - _HEADER_H) // GLYPH_H
            idx = b.scroll_top + row
            if 0 <= idx < len(b.entries):
                b.selected = idx
                b.redraw()
                # Double-click? Same row, within 500 ms → open.
                import time
                now = time.monotonic() if hasattr(time, "monotonic") else 0.0
                if last_click[1] == idx and now - last_click[0] < 0.5:
                    pending = "enter"
                last_click[0] = now
                last_click[1] = idx
            return
        if ev.kind != _gui_input.EVENT_KEY_DOWN:
            return
        if ev.code == _gui_input.KEY_ESC:
            closed = True
        elif ev.code == _gui_input.KEY_DOWN:
            b._move(+1); b.redraw()
        elif ev.code == _gui_input.KEY_UP:
            b._move(-1); b.redraw()
        elif ev.code == _gui_input.KEY_PAGE_DOWN:
            b._move(+b.list_rows); b.redraw()
        elif ev.code == _gui_input.KEY_PAGE_UP:
            b._move(-b.list_rows); b.redraw()
        elif ev.code == _gui_input.KEY_ENTER:
            pending = "enter"
        elif ev.code == _gui_input.KEY_BACKSPACE:
            pending = "back"

    win.set_event_handler(on_event)

    while not closed and not win._closed:
        if pending == "enter":
            pending = None
            await b._enter()
            b.redraw()
        elif pending == "back":
            pending = None
            if b.cwd != "/":
                parts = [p for p in b.cwd.split("/") if p]
                b.cwd = "/" + "/".join(parts[:-1])
                if not b.cwd: b.cwd = "/"
                await b.reload()
                b.redraw()
        await asyncio.sleep(0.03)
    win.close()


from apps._icons import files_icon

registry.register(
    name="files",
    description="Arrow-key file browser",
    entry=main,
    icon_factory=files_icon,
)

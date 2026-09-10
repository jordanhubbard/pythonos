"""Reusable filesystem browser and Open/Save chooser view.

The chooser deliberately uses the same small :mod:`kernel.gui.ui` hierarchy
as applications.  The Files app is just a host for this view; editors and
future applications can use :func:`choose_file` without reimplementing
filesystem navigation.
"""

from __future__ import annotations

import asyncio
import time

from kernel.display.font import GLYPH_H, GLYPH_W
from kernel.fs.vfs import InodeType, vfs
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect
from kernel.gui.ui import ListView


_BG = 0x101820
_FG = 0xCCCCCC
_DIM = 0x808080
_BAR_BG = 0x355088
_BAR_FG = 0xFFFFFF
_HEADER_H = GLYPH_H + 4
_FOOTER_H = GLYPH_H + 4
_DOUBLE_CLICK_SECONDS = 0.5


def split_path(path: str | None, default_name: str = "") -> tuple[str, str]:
    """Return a chooser starting directory and filename for *path*."""
    value = (path or "").strip()
    if not value:
        return "/home", default_name
    if value.endswith("/"):
        return value.rstrip("/") or "/", default_name
    directory, _, name = value.rpartition("/")
    return directory or "/", name or default_name


class FileChooserView(ListView):
    """A keyboard-and-mouse navigable Open/Save view.

    ``result`` becomes an absolute path when accepted. ``done`` is also set
    when the user cancels, in which case ``result`` remains ``None``.
    """

    def __init__(self, host, *, mode: str = "open", start_path: str = "/",
                 filename: str = "", allow_transfer: bool = False,
                 extensions: tuple[str, ...] | None = None) -> None:
        if mode not in ("open", "save"):
            raise ValueError("file chooser mode must be 'open' or 'save'")
        super().__init__(0, 0, host.w, host.h, background=_BG, host=host)
        self.win = host
        host.add(self)
        self.mode = mode
        self.cwd = start_path.rstrip("/") or "/"
        self.filename = filename
        self.allow_transfer = allow_transfer
        self.extensions = tuple(ext.lower() for ext in extensions) if extensions else None
        self.message = ""
        self.entries: list[tuple[str, str]] = []
        self.items = self.entries
        self.cols = max(1, host.w // GLYPH_W)
        self.list_rows = max(
            1, (host.h - _HEADER_H - _FOOTER_H) // GLYPH_H)
        self.result: str | None = None
        self.done = False
        self._last_click_time = 0.0
        self._last_click_index = -1
        self._filename_editing = False
        self._drag_start: tuple[int, int, int] | None = None
        self._dragging = False
        if allow_transfer:
            host.accepts_host_file_drop = True

    def _path(self, name: str) -> str:
        return self.cwd.rstrip("/") + "/" + name if self.cwd != "/" else "/" + name

    async def reload(self) -> None:
        try:
            names = await vfs.readdir(self.cwd)
        except Exception:
            names = []
        rows = []
        for name in sorted(n for n in names if n not in (".", "..")):
            kind = "?"
            try:
                inode_type = getattr(await vfs.stat(self._path(name)),
                                     "inode_type", None)
                if inode_type == InodeType.DIR:
                    kind = "dir"
                elif inode_type == InodeType.FILE:
                    kind = "file"
                elif inode_type == InodeType.SYMLINK:
                    kind = "link"
            except Exception:
                pass
            if (kind == "file" and self.extensions is not None
                    and not name.lower().endswith(self.extensions)):
                continue
            rows.append((name, kind))
        if self.cwd != "/":
            rows.insert(0, ("..", "dir"))
        self.entries = rows
        self.items = rows
        self.selected = 0
        self.scroll_top = 0
        self._last_click_index = -1
        self.invalidate()

    def _ensure_visible(self) -> None:
        if self.selected < self.scroll_top:
            self.scroll_top = self.selected
        elif self.selected >= self.scroll_top + self.list_rows:
            self.scroll_top = self.selected - self.list_rows + 1

    def move_selection(self, delta: int) -> None:
        super().move_selection(delta)
        self._ensure_visible()
        self._filename_editing = False

    async def go_parent(self) -> None:
        if self.cwd == "/":
            return
        parts = [part for part in self.cwd.split("/") if part]
        self.cwd = "/" + "/".join(parts[:-1])
        if not self.cwd:
            self.cwd = "/"
        await self.reload()

    async def activate_selected(self) -> None:
        if self._filename_editing and self.mode == "save":
            self.accept_filename()
            return
        if not self.entries:
            return
        name, kind = self.entries[self.selected]
        if name == "..":
            await self.go_parent()
        elif kind == "dir":
            self.cwd = self._path(name)
            await self.reload()
        elif kind in ("file", "link"):
            self.filename = name
            self.result = self._path(name)
            self.done = True
        self.redraw()

    def accept_filename(self) -> None:
        name = self.filename.strip()
        if self.mode == "save" and name and "/" not in name:
            self.result = self._path(name)
            self.done = True

    def cancel(self) -> None:
        self.result = None
        self.done = True

    def _schedule(self, operation) -> None:
        asyncio.get_event_loop().create_task(operation)

    async def _receive_drop(self, ev) -> None:
        from kernel.gui.filetransfer import import_host_file
        destination = self.cwd
        idx = self._row_at(ev.y)
        if 0 <= idx < len(self.entries):
            name, kind = self.entries[idx]
            if kind == "dir" and name != "..":
                destination = self._path(name)
        self.message = "Importing " + ev.name + "…"
        self.redraw()
        try:
            path, count = await import_host_file(ev.token, ev.name, ev.size,
                                                  destination)
            self.message = "Imported " + str(count) + " bytes: " + path
            await self.reload()
        except Exception as exc:
            self.message = "Import failed: " + str(exc)
        self.redraw()

    async def _export_selected(self) -> None:
        if not self.entries:
            return
        name, kind = self.entries[self.selected]
        if kind not in ("file", "link"):
            self.message = "Select a file to export"
            self.redraw()
            return
        from kernel.gui.filetransfer import export_guest_file
        path = self._path(name)
        self.message = "Exporting " + path + "…"
        self.redraw()
        try:
            host_path, count = await export_guest_file(path)
            self.message = "Exported " + str(count) + " bytes to " + host_path
        except Exception as exc:
            self.message = "Export failed: " + str(exc)
        self.redraw()

    def _row_at(self, y: int) -> int:
        if y < _HEADER_H or y >= self.h - _FOOTER_H:
            return -1
        return self.scroll_top + (y - _HEADER_H) // GLYPH_H

    def on_event(self, ev) -> bool:
        if super().on_event(ev):
            self.redraw()
            return True
        if ev.kind == _gui_input.HOST_FILE_DROP and self.allow_transfer:
            self._schedule(self._receive_drop(ev))
            return True

        if ev.kind == _gui_input.MOUSE_DOWN and ev.code == 1:
            idx = self._row_at(ev.y)
            if 0 <= idx < len(self.entries):
                now = time.monotonic()
                double = (idx == self._last_click_index and
                          now - self._last_click_time < _DOUBLE_CLICK_SECONDS)
                self.selected = idx
                self._ensure_visible()
                name, kind = self.entries[idx]
                if self.mode == "save" and kind in ("file", "link"):
                    self.filename = name
                self._filename_editing = False
                self._last_click_time = now
                self._last_click_index = idx
                self._drag_start = (idx, ev.x, ev.y)
                self._dragging = False
                if double:
                    self._schedule(self.activate_selected())
                self.redraw()
                return True

            # Footer buttons are intentionally plain text so this remains a
            # useful first-principles example rather than a widget toolkit.
            if ev.y >= self.h - _FOOTER_H:
                if self.allow_transfer and ev.x < 12 * GLYPH_W:
                    self._schedule(self._export_selected())
                if ev.x >= self.w - 12 * GLYPH_W:
                    self.cancel()
                elif ev.x >= self.w - 24 * GLYPH_W:
                    if self.mode == "save":
                        self.accept_filename()
                    else:
                        self._schedule(self.activate_selected())
                self.redraw()
                return True
            return False

        if ev.kind == _gui_input.MOUSE_MOVE and self._drag_start is not None:
            _idx, start_x, start_y = self._drag_start
            if abs(ev.x - start_x) + abs(ev.y - start_y) >= 6:
                self._dragging = True
                self.message = "Drop on Export to copy this file to the host"
                self.redraw()
            return self._dragging

        if ev.kind == _gui_input.MOUSE_UP and ev.code == 1:
            dragging = self._dragging
            self._drag_start = None
            self._dragging = False
            if dragging and ev.y >= self.h - _FOOTER_H and self.allow_transfer:
                self._schedule(self._export_selected())
                return True

        if ev.kind != _gui_input.EVENT_KEY_DOWN:
            return False
        if ev.code == _gui_input.KEY_ESC:
            self.cancel()
        elif ev.code == _gui_input.KEY_DOWN:
            self.move_selection(1)
        elif ev.code == _gui_input.KEY_UP:
            self.move_selection(-1)
        elif ev.code == _gui_input.KEY_PAGE_DOWN:
            self.move_selection(self.list_rows)
        elif ev.code == _gui_input.KEY_PAGE_UP:
            self.move_selection(-self.list_rows)
        elif ev.code == _gui_input.KEY_ENTER:
            self._schedule(self.activate_selected())
        elif ev.code == _gui_input.KEY_BACKSPACE:
            if self.mode == "save" and self._filename_editing:
                self.filename = self.filename[:-1]
            else:
                self._schedule(self.go_parent())
        elif self.mode == "save" and ev.text:
            for char in ev.text:
                if char >= " " and ord(char) < 0x7F and char != "/":
                    if not self._filename_editing:
                        self.filename = ""
                        self._filename_editing = True
                    self.filename += char
        self.redraw()
        return True

    def redraw(self) -> None:
        surface = self.win.surface
        SDL_FillRect(surface, None, _BG)
        surface._fill_rect(0, 0, self.w, _HEADER_H, _BAR_BG)
        surface.draw_text(4, 2, ("path: " + self.cwd)[:self.cols],
                          fg=_BAR_FG, bg=_BAR_BG)
        for row in range(self.list_rows):
            idx = self.scroll_top + row
            y = _HEADER_H + row * GLYPH_H
            selected = idx == self.selected
            bg = _BAR_BG if selected else _BG
            fg = _BAR_FG if selected else _FG
            surface._fill_rect(0, y, self.w, GLYPH_H, bg)
            if idx < len(self.entries):
                name, kind = self.entries[idx]
                label = name + ("/" if kind == "dir" else "")
                surface.draw_text(4, y, label[:self.cols - 8], fg=fg, bg=bg)
                tag = ("  " + kind).rjust(6)
                surface.draw_text(self.w - len(tag) * GLYPH_W - 4, y, tag,
                                  fg=_BAR_FG if selected else _DIM, bg=bg)

        fy = self.h - _FOOTER_H
        surface._fill_rect(0, fy, self.w, _FOOTER_H, _BAR_BG)
        if self.mode == "save":
            prompt = "File: " + self.filename + ("_" if self._filename_editing else "")
        elif self.message:
            prompt = self.message
        else:
            prompt = "Double-click a file to open"
        action = "[ Save ]" if self.mode == "save" else "[ Open ]"
        buttons = action + "  [ Cancel ]"
        prompt_x = 13 * GLYPH_W if self.allow_transfer else 4
        prompt_cols = max(1, self.cols - 39 if self.allow_transfer
                          else self.cols - 26)
        surface.draw_text(prompt_x, fy + 2, prompt[:prompt_cols],
                          fg=_BAR_FG, bg=_BAR_BG)
        surface.draw_text(max(4, self.w - len(buttons) * GLYPH_W - 4), fy + 2,
                          buttons, fg=_BAR_FG, bg=_BAR_BG)
        if self.allow_transfer:
            surface.draw_text(4, fy + 2, "[ Export ]", fg=_BAR_FG, bg=_BAR_BG)
        self.invalidate()


async def choose_file(*, title: str = "Open File", mode: str = "open",
                      path: str | None = None,
                      default_name: str = "untitled.txt",
                      allow_transfer: bool = False,
                      extensions: tuple[str, ...] | None = None) -> str | None:
    """Show a chooser window and return its selected path, or ``None``."""
    from kernel.gui.compositor import CompositorWindow, compositor

    start_path, filename = split_path(path, default_name)
    desk_w, desk_h = compositor._desktop_size()
    w, h = min(720, desk_w - 60), min(500, desk_h - 100)
    win = CompositorWindow(title, x=max(30, (desk_w - w) // 2),
                           y=max(40, (desk_h - h) // 2), w=w, h=h)
    chooser = FileChooserView(win, mode=mode, start_path=start_path,
                              filename=filename,
                              allow_transfer=allow_transfer,
                              extensions=extensions)
    compositor.add_window(win)
    await chooser.reload()
    chooser.redraw()

    def on_event(event):
        chooser.on_event(event)

    win.set_event_handler(on_event)
    while not chooser.done and not win._closed:
        await asyncio.sleep(0.03)
    result = chooser.result
    win.close()
    compositor.remove_window(win)
    return result

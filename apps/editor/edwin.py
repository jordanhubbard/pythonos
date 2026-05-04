"""apps.editor.edwin — minimal screen-mode text editor.

Implements just enough emacs-flavored editing to be useful:

    Arrows / Home / End / PgUp / PgDn   move cursor
    Backspace / Delete                  remove characters
    Enter                               split line
    Printable keys                      insert
    Ctrl-S                              save
    Ctrl-Q  /  Ctrl-X Ctrl-C            quit (close window)
    Ctrl-G                              cancel pending Ctrl-X prefix

The buffer is a list of strings (one per line) — fine for the small
files this kernel is realistically going to edit.
"""

import asyncio

from kernel.fs.vfs import vfs, OpenFlags
from kernel.gui.compositor import compositor, CompositorWindow
from kernel.gui import input as _gui_input
from kernel.gui.sdl2.surface import SDL_FillRect
from kernel.display.font import GLYPH_W, GLYPH_H
from apps import registry


_BG          = 0x101820
_FG          = 0xCCCCCC
_STATUS_BG   = 0x224488
_STATUS_FG   = 0xFFFFFF
_CURSOR      = 0xFFCC00
_HEADER_H    = GLYPH_H + 4
_FOOTER_H    = GLYPH_H + 4


class _Editor:
    def __init__(self, win: CompositorWindow, path: str | None) -> None:
        self.win = win
        self.path = path
        self.lines: list[str] = [""]
        self.cy = 0      # buffer row
        self.cx = 0      # buffer col
        self.scroll = 0  # top buffer row visible
        self.dirty = False
        self.message = ""
        self.cols = max(1, win.w // GLYPH_W)
        self.rows = max(1, (win.h - _HEADER_H - _FOOTER_H) // GLYPH_H)
        self.ctrl_x_pending = False

    # ── Persistence ──────────────────────────────────────────────────────

    async def load(self) -> None:
        if not self.path:
            self.message = "(new file)"
            return
        try:
            data = await _read_all(self.path)
            text = data.decode("utf-8", errors="replace")
            self.lines = text.split("\n") if text else [""]
            if not self.lines:
                self.lines = [""]
            self.message = f"loaded {self.path} ({len(text)} bytes)"
        except FileNotFoundError:
            self.message = f"(new file: {self.path})"
        except Exception as e:
            self.message = f"load failed: {e}"

    async def save(self) -> None:
        if not self.path:
            self.message = "no filename"
            return
        try:
            text = "\n".join(self.lines).encode("utf-8")
            await _write_all(self.path, text)
            self.dirty = False
            self.message = f"saved {self.path} ({len(text)} bytes)"
        except Exception as e:
            self.message = f"save failed: {e}"

    # ── Drawing ──────────────────────────────────────────────────────────

    def _ensure_visible(self) -> None:
        if self.cy < self.scroll:
            self.scroll = self.cy
        elif self.cy >= self.scroll + self.rows:
            self.scroll = self.cy - self.rows + 1

    def redraw(self) -> None:
        s = self.win.surface
        SDL_FillRect(s, None, _BG)
        # Header — file path + dirty flag.
        s._fill_rect(0, 0, self.win.w, _HEADER_H, _STATUS_BG)
        title = (self.path or "(no file)") + (" *" if self.dirty else "")
        s.draw_text(4, 2, title[: self.cols], fg=_STATUS_FG, bg=_STATUS_BG)
        # Body rows.
        for r in range(self.rows):
            buf_row = self.scroll + r
            y = _HEADER_H + r * GLYPH_H
            if 0 <= buf_row < len(self.lines):
                line = self.lines[buf_row][: self.cols]
                if line:
                    s.draw_text(0, y, line, fg=_FG, bg=_BG)
        # Cursor — solid block.
        cx = max(0, min(self.cols - 1, self.cx))
        cy = self.cy - self.scroll
        if 0 <= cy < self.rows:
            cy_pix = _HEADER_H + cy * GLYPH_H
            s._fill_rect(cx * GLYPH_W, cy_pix, GLYPH_W, GLYPH_H, _CURSOR)
            # Re-draw the underlying glyph in inverted color.
            if self.cy < len(self.lines) and cx < len(self.lines[self.cy]):
                ch = self.lines[self.cy][cx]
                s.draw_char(cx * GLYPH_W, cy_pix, ch, fg=_BG, bg=_CURSOR)
        # Footer — message bar.
        fy = self.win.h - _FOOTER_H
        s._fill_rect(0, fy, self.win.w, _FOOTER_H, _STATUS_BG)
        line = f" L{self.cy + 1} C{self.cx + 1}  "
        if self.ctrl_x_pending:
            line += "C-x  "
        line += self.message
        s.draw_text(4, fy + 2, line[: self.cols], fg=_STATUS_FG, bg=_STATUS_BG)
        self.win.dirty = True

    # ── Editing primitives ──────────────────────────────────────────────

    def _insert_char(self, ch: str) -> None:
        line = self.lines[self.cy]
        self.lines[self.cy] = line[: self.cx] + ch + line[self.cx:]
        self.cx += 1
        self.dirty = True

    def _newline(self) -> None:
        line = self.lines[self.cy]
        self.lines[self.cy] = line[: self.cx]
        self.lines.insert(self.cy + 1, line[self.cx:])
        self.cy += 1
        self.cx = 0
        self.dirty = True

    def _backspace(self) -> None:
        if self.cx > 0:
            line = self.lines[self.cy]
            self.lines[self.cy] = line[: self.cx - 1] + line[self.cx:]
            self.cx -= 1
        elif self.cy > 0:
            prev = self.lines[self.cy - 1]
            cur  = self.lines[self.cy]
            self.cx = len(prev)
            self.lines[self.cy - 1] = prev + cur
            del self.lines[self.cy]
            self.cy -= 1
        else:
            return
        self.dirty = True

    def _delete(self) -> None:
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[: self.cx] + line[self.cx + 1:]
            self.dirty = True
        elif self.cy + 1 < len(self.lines):
            self.lines[self.cy] = line + self.lines[self.cy + 1]
            del self.lines[self.cy + 1]
            self.dirty = True

    def _clamp_x(self) -> None:
        self.cx = min(self.cx, len(self.lines[self.cy]))

    # ── Event handling ──────────────────────────────────────────────────

    def on_event(self, ev) -> bool:
        """Returns True if the editor should keep running."""
        if ev.kind != _gui_input.KEY_DOWN:
            return True
        self.message = ""
        c = ev.code
        ctrl = bool(ev.mods & _gui_input.MOD_CTRL)

        if self.ctrl_x_pending:
            self.ctrl_x_pending = False
            if ctrl and ev.text == "\x03":   # C-x C-c
                return False
            if ctrl and ev.text == "\x13":   # C-x C-s
                asyncio.get_event_loop().create_task(self.save())
                return True
            self.message = "Quit"
            return True

        if ctrl and ev.text:
            byte = ord(ev.text[0]) if ev.text else 0
            if byte == 24:   # C-x prefix
                self.ctrl_x_pending = True
                return True
            if byte == 19:   # C-s
                asyncio.get_event_loop().create_task(self.save())
                return True
            if byte == 17:   # C-q
                return False
            if byte == 7:    # C-g
                self.message = "Cancel"
                return True

        if c == _gui_input.KEY_LEFT:
            if self.cx > 0:
                self.cx -= 1
            elif self.cy > 0:
                self.cy -= 1
                self.cx = len(self.lines[self.cy])
        elif c == _gui_input.KEY_RIGHT:
            if self.cx < len(self.lines[self.cy]):
                self.cx += 1
            elif self.cy + 1 < len(self.lines):
                self.cy += 1
                self.cx = 0
        elif c == _gui_input.KEY_UP:
            if self.cy > 0:
                self.cy -= 1
                self._clamp_x()
        elif c == _gui_input.KEY_DOWN:
            if self.cy + 1 < len(self.lines):
                self.cy += 1
                self._clamp_x()
        elif c == _gui_input.KEY_HOME:
            self.cx = 0
        elif c == _gui_input.KEY_END:
            self.cx = len(self.lines[self.cy])
        elif c == _gui_input.KEY_PAGE_UP:
            self.cy = max(0, self.cy - self.rows)
            self._clamp_x()
        elif c == _gui_input.KEY_PAGE_DOWN:
            self.cy = min(len(self.lines) - 1, self.cy + self.rows)
            self._clamp_x()
        elif c == _gui_input.KEY_ENTER:
            self._newline()
        elif c == _gui_input.KEY_BACKSPACE:
            self._backspace()
        elif c == _gui_input.KEY_DELETE:
            self._delete()
        elif c == _gui_input.KEY_TAB:
            self._insert_char("\t")
        elif ev.text:
            for ch in ev.text:
                if ch >= " " and ord(ch) < 0x7F:
                    self._insert_char(ch)
        self._ensure_visible()
        return True


# ── Async file I/O — same shape as kernel.commands._read_all/_write_all ──
# vfs.close() is sync (returns None); awaiting it raises TypeError. Don't.

async def _read_all(path: str) -> bytes:
    fd = await vfs.open(path, OpenFlags.RDONLY)
    try:
        chunks = []
        while True:
            chunk = await vfs.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        vfs.close(fd)


async def _write_all(path: str, data: bytes) -> None:
    fd = await vfs.open(path,
                          OpenFlags.WRONLY | OpenFlags.CREAT | OpenFlags.TRUNC)
    try:
        off = 0
        while off < len(data):
            n = await vfs.write(fd, data[off:])
            if n <= 0:
                break
            off += n
    finally:
        vfs.close(fd)


async def main(argv=None, *args, **kwargs) -> None:
    argv = list(argv) if argv else []
    path = argv[0] if argv else None
    title = "Editor: " + (path or "(no file)")
    win = CompositorWindow(title, x=120, y=120, w=720, h=480)
    compositor.add_window(win)
    ed = _Editor(win, path)
    await ed.load()
    ed.redraw()

    running = True

    def on_event(ev):
        nonlocal running
        if not ed.on_event(ev):
            running = False
        ed.redraw()

    win.set_event_handler(on_event)

    while running and not win._closed:
        await asyncio.sleep(0.03)
    win.close()


from apps._icons import editor_icon

registry.register(
    name="editor",
    description="Screen-mode text editor",
    entry=main,
    icon_factory=editor_icon,
)

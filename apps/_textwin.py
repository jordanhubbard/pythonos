"""apps._textwin — Shared text-grid window for terminal-style apps.

Wraps a :class:`kernel.gui.compositor.CompositorWindow` in a
fixed-pitch text grid backed by the bundled 8x16 bitmap font.

Public API:
    write(text)           render glyphs and advance the cursor
    write_raw(bytes)      decode and forward to write
    read_char()           async coroutine returning one char (string)
    read_byte()           async coroutine returning one raw byte (int)

The byte stream emits ANSI / VT100-ish sequences for non-printable
keys (Esc-based for arrows, Home/End, etc.), so the kernel linenoise
shim drives line editing transparently when both `read_byte` and
`write_raw` are wired into Shell / Editor.
"""

import asyncio

from kernel.display.font import GLYPH_W, GLYPH_H
from kernel.gui import input as _gui_input
from kernel.gui.compositor import CompositorWindow
from kernel.gui.sdl2.surface import SDL_FillRect


# Map kernel keycodes to ANSI byte sequences linenoise understands.
_KEYCODE_BYTES: dict[int, bytes] = {
    _gui_input.KEY_LEFT:      b"\x1b[D",
    _gui_input.KEY_RIGHT:     b"\x1b[C",
    _gui_input.KEY_UP:        b"\x1b[A",
    _gui_input.KEY_DOWN:      b"\x1b[B",
    _gui_input.KEY_HOME:      b"\x1b[H",
    _gui_input.KEY_END:       b"\x1b[F",
    _gui_input.KEY_DELETE:    b"\x1b[3~",
    _gui_input.KEY_PAGE_UP:   b"\x1b[5~",
    _gui_input.KEY_PAGE_DOWN: b"\x1b[6~",
}


class TextWin:
    """Text terminal inside a CompositorWindow with a blinking cursor."""

    CURSOR_BLINK_HZ = 2

    def __init__(self, window: CompositorWindow,
                 fg: int = 0xCCCCCC, bg: int = 0x101010,
                 cursor_color: int = 0xCCCCCC) -> None:
        self.win = window
        self.fg = fg
        self.bg = bg
        self.cursor_color = cursor_color
        self.cols = max(1, window.w // GLYPH_W)
        self.rows = max(1, window.h // GLYPH_H)
        self.cur_x = 0
        self.cur_y = 0
        self._cursor_visible = False
        self._cursor_drawn_at: tuple[int, int] | None = None
        self._byte_q: asyncio.Queue = asyncio.Queue()
        self._char_q: asyncio.Queue = asyncio.Queue()
        SDL_FillRect(window.surface, None, self.bg)
        window.dirty = True
        try:
            asyncio.get_event_loop().create_task(self._cursor_blink())
        except RuntimeError:
            pass

    # ── Cursor ──────────────────────────────────────────────────────────

    def _erase_cursor(self) -> None:
        if self._cursor_drawn_at is None:
            return
        cx, cy = self._cursor_drawn_at
        self.win.surface._fill_rect(cx * GLYPH_W, cy * GLYPH_H,
                                     GLYPH_W, GLYPH_H, self.bg)
        self._cursor_drawn_at = None
        self.win.dirty = True

    def _draw_cursor(self) -> None:
        x = self.cur_x * GLYPH_W
        y = self.cur_y * GLYPH_H + GLYPH_H - 2
        self.win.surface._fill_rect(x, y, GLYPH_W, 2, self.cursor_color)
        self._cursor_drawn_at = (self.cur_x, self.cur_y)
        self.win.dirty = True

    async def _cursor_blink(self) -> None:
        period = 1.0 / (2 * self.CURSOR_BLINK_HZ)
        while not self.win._closed:
            self._cursor_visible = not self._cursor_visible
            if self._cursor_visible:
                self._erase_cursor()
                self._draw_cursor()
            else:
                self._erase_cursor()
            await asyncio.sleep(period)

    # ── Drawing ─────────────────────────────────────────────────────────

    def _scroll_up(self) -> None:
        # No backing buffer; clear and move cursor up to last row.
        SDL_FillRect(self.win.surface, None, self.bg)
        self.cur_y = self.rows - 1
        self.cur_x = 0
        self._cursor_drawn_at = None
        self.win.dirty = True

    def _draw_glyph_at(self, col: int, row: int, ch: str) -> None:
        self.win.surface.draw_char(col * GLYPH_W, row * GLYPH_H,
                                    ch, fg=self.fg, bg=self.bg)

    def _erase_glyph_at(self, col: int, row: int) -> None:
        self.win.surface._fill_rect(col * GLYPH_W, row * GLYPH_H,
                                     GLYPH_W, GLYPH_H, self.bg)

    def clear(self) -> None:
        SDL_FillRect(self.win.surface, None, self.bg)
        self.cur_x = 0
        self.cur_y = 0
        self._cursor_drawn_at = None
        self.win.dirty = True

    # ── Public callables ────────────────────────────────────────────────

    def write(self, text: str) -> None:
        self._erase_cursor()
        for ch in text:
            if ch == "\n":
                self.cur_x = 0
                self.cur_y += 1
            elif ch == "\r":
                self.cur_x = 0
            elif ch == "\b":
                if self.cur_x > 0:
                    self.cur_x -= 1
                    self._erase_glyph_at(self.cur_x, self.cur_y)
            elif ch == "\t":
                stop = (self.cur_x // 8 + 1) * 8
                while self.cur_x < stop and self.cur_x < self.cols:
                    self._draw_glyph_at(self.cur_x, self.cur_y, " ")
                    self.cur_x += 1
            elif 32 <= ord(ch) < 127:
                if self.cur_x >= self.cols:
                    self.cur_x = 0
                    self.cur_y += 1
                self._draw_glyph_at(self.cur_x, self.cur_y, ch)
                self.cur_x += 1
            if self.cur_y >= self.rows:
                self._scroll_up()
        self.win.dirty = True
        if self._cursor_visible:
            self._draw_cursor()

    def write_raw(self, data: bytes) -> None:
        self.write(data.decode("utf-8", errors="replace"))

    async def read_char(self) -> str:
        return await self._char_q.get()

    async def read_byte(self) -> int:
        return await self._byte_q.get()

    # ── Event ingestion ─────────────────────────────────────────────────

    def on_event(self, ev) -> None:
        if ev.kind != _gui_input.KEY_DOWN:
            return
        # Special keys → byte sequence + a representative char.
        seq = _KEYCODE_BYTES.get(ev.code)
        if seq is not None:
            for b in seq:
                self._byte_q.put_nowait(b)
            self._char_q.put_nowait(seq.decode("ascii", errors="replace"))
            return
        if ev.code == _gui_input.KEY_ENTER:
            self._byte_q.put_nowait(13)        # CR — linenoise expects this
            self._char_q.put_nowait("\n")
            return
        if ev.code == _gui_input.KEY_BACKSPACE:
            self._byte_q.put_nowait(0x7F)      # DEL — what TTYs emit, what
                                                #       linenoise listens for
            self._char_q.put_nowait("\b")
            return
        if ev.code == _gui_input.KEY_TAB:
            self._byte_q.put_nowait(9)
            self._char_q.put_nowait("\t")
            return
        if ev.code == _gui_input.KEY_ESC:
            self._byte_q.put_nowait(27)
            self._char_q.put_nowait("\x1b")
            return
        if ev.text:
            for b in ev.text.encode("utf-8", errors="replace"):
                self._byte_q.put_nowait(b)
            self._char_q.put_nowait(ev.text)

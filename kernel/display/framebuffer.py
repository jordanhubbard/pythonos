"""
kernel.display.framebuffer — Linear framebuffer rendering.

Writes directly to the physical framebuffer via _hal.mmio_write32().
Supports 32-bit (XRGB8888) and 24-bit colour modes.

Coordinate system: (0,0) = top-left.
"""


from dataclasses import dataclass
from kernel.hal.io import (
    mmio_write32, mmio_read32,
    mmio_fill32, mmio_write_buf32,
    buf_fill32, buf_fill32_at,
)
from kernel.display.font import get_glyph, GLYPH_W, GLYPH_H


# ── Colour helpers ────────────────────────────────────────────────────────────

def rgb(r: int, g: int, b: int) -> int:
    return (r << 16) | (g << 8) | b

BLACK   = rgb(0,   0,   0)
WHITE   = rgb(255, 255, 255)
GREEN   = rgb(0,   255, 0)
RED     = rgb(255, 0,   0)
BLUE    = rgb(0,   0,   255)
YELLOW  = rgb(255, 255, 0)
CYAN    = rgb(0,   255, 255)
MAGENTA = rgb(255, 0,   255)
GREY    = rgb(128, 128, 128)
DARK    = rgb(20,  20,  20)


# ── Surface — off-screen pixel buffer ────────────────────────────────────────

class Surface:
    """
    Software-rendered pixel buffer. Blit to Framebuffer when ready.
    Backed by a Python bytearray (no MMIO until blit).
    """

    def __init__(self, width: int, height: int, bg: int = BLACK) -> None:
        self.width  = width
        self.height = height
        self._buf   = bytearray(width * height * 4)
        if bg:
            self.fill(bg)

    def _offset(self, x: int, y: int) -> int:
        return (y * self.width + x) * 4

    def put_pixel(self, x: int, y: int, colour: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            o = self._offset(x, y)
            self._buf[o]     =  colour        & 0xFF  # B
            self._buf[o + 1] = (colour >> 8)  & 0xFF  # G
            self._buf[o + 2] = (colour >> 16) & 0xFF  # R
            self._buf[o + 3] = 0xFF                   # X

    def get_pixel(self, x: int, y: int) -> int:
        o = self._offset(x, y)
        return (self._buf[o + 2] << 16) | (self._buf[o + 1] << 8) | self._buf[o]

    def fill(self, colour: int) -> None:
        # In-place uint32 fill via the C HAL primitive. Avoids the
        # `bytes(...) * (w*h)` allocation that dominated the per-frame
        # cost of a 1024x768 back-buffer compose.
        word = ((colour & 0xFFFFFF) | 0xFF000000)  # XRGB → BGRX little-endian
        buf_fill32(self._buf, word)

    def fill_rect(self, x: int, y: int, w: int, h: int, colour: int) -> None:
        x1 = max(0, x);       y1 = max(0, y)
        x2 = min(self.width, x + w)
        y2 = min(self.height, y + h)
        if x2 <= x1 or y2 <= y1:
            return
        word = ((colour & 0xFFFFFF) | 0xFF000000)
        span = x2 - x1
        for row in range(y1, y2):
            buf_fill32_at(self._buf, (row * self.width + x1) * 4, span, word)

    def _fill_rect(self, x: int, y: int, w: int, h: int, colour: int) -> None:
        """Alias so MenuBar.render can paint onto a local Surface."""
        self.fill_rect(x, y, w, h, colour)

    def blit_buffer(self, buf, src_w: int, src_h: int,
                     dst_x: int, dst_y: int) -> None:
        """Copy a BGRX-formatted bytes-like buffer (src_w*src_h*4 bytes)
        into this surface at (dst_x, dst_y). Used by the compositor to
        composite window pixels into an off-screen back buffer."""
        sy0 = max(0, -dst_y)
        sy1 = min(src_h, self.height - dst_y)
        if sy1 <= sy0:
            return
        sx0 = max(0, -dst_x)
        sx1 = min(src_w, self.width - dst_x)
        if sx1 <= sx0:
            return
        row_bytes = (sx1 - sx0) * 4
        for sy in range(sy0, sy1):
            dy = dst_y + sy
            src_off = (sy * src_w + sx0) * 4
            dst_off = (dy * self.width + dst_x + sx0) * 4
            self._buf[dst_off:dst_off + row_bytes] = (
                buf[src_off:src_off + row_bytes]
            )

    def draw_char(self, x: int, y: int, char: str,
                  fg: int = WHITE, bg: int | None = None) -> None:
        glyph = get_glyph(char)
        for row, byte in enumerate(glyph):
            for col in range(8):
                if byte & (0x80 >> col):
                    self.put_pixel(x + col, y + row, fg)
                elif bg is not None:
                    self.put_pixel(x + col, y + row, bg)

    def draw_text(self, x: int, y: int, text: str,
                  fg: int = WHITE, bg: int | None = None) -> int:
        """Draw string; returns x position after last character."""
        cx = x
        for ch in text:
            if ch == '\n':
                y += GLYPH_H
                cx = x
            else:
                self.draw_char(cx, y, ch, fg, bg)
                cx += GLYPH_W
        return cx


# ── Framebuffer ───────────────────────────────────────────────────────────────

class Framebuffer:
    """
    Direct-write linear framebuffer.

    All pixel writes go to physical memory via mmio_write32.
    For animated content, render to a Surface first and blit.
    """

    def __init__(self, info: dict) -> None:
        self.phys   = info["phys_addr"]
        self.pitch  = info["pitch"]
        self.width  = info["width"]
        self.height = info["height"]
        self.bpp    = info["bpp"]
        self._bytes_per_pixel = self.bpp // 8

    def _pixel_addr(self, x: int, y: int) -> int:
        return self.phys + y * self.pitch + x * self._bytes_per_pixel

    def put_pixel(self, x: int, y: int, colour: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            mmio_write32(self._pixel_addr(x, y), colour)

    def fill(self, colour: int) -> None:
        # Whole framebuffer is contiguous when pitch == width*bpp (the
        # common case for ramfb and bochs-VBE). Fall back to per-row
        # bulk fills if pitch has padding.
        bpp = self._bytes_per_pixel
        if self.pitch == self.width * bpp:
            mmio_fill32(self.phys, self.width * self.height, colour)
            return
        for y in range(self.height):
            mmio_fill32(self.phys + y * self.pitch, self.width, colour)

    def present(self, buf) -> None:
        """Bulk-flush a back buffer (BGRX, width*height*4 bytes) to the
        visible framebuffer in a single MMIO write per frame. The whole
        composited image becomes visible at once — no flicker between a
        clear and the per-window blits."""
        bpp = self._bytes_per_pixel
        if self.pitch == self.width * bpp:
            mmio_write_buf32(self.phys, buf)
            return
        # Paddedpitch: copy row by row.
        row_bytes = self.width * 4
        mv = memoryview(buf)
        for y in range(self.height):
            src_off = y * row_bytes
            mmio_write_buf32(self.phys + y * self.pitch,
                              mv[src_off:src_off + row_bytes])

    def fill_rect(self, x: int, y: int, w: int, h: int, colour: int) -> None:
        x1 = max(0, x); y1 = max(0, y)
        x2 = min(self.width, x + w); y2 = min(self.height, y + h)
        if x2 <= x1 or y2 <= y1:
            return
        bpp = self._bytes_per_pixel
        span = x2 - x1
        for row in range(y1, y2):
            mmio_fill32(self.phys + row * self.pitch + x1 * bpp,
                        span, colour)

    def blit(self, surface: Surface, dst_x: int = 0, dst_y: int = 0) -> None:
        """Copy a kernel.display.Surface pixel buffer to the framebuffer."""
        self.blit_buffer(surface._buf, surface.width, surface.height,
                          dst_x, dst_y)

    def blit_buffer(self, buf, src_w: int, src_h: int,
                     dst_x: int, dst_y: int) -> None:
        """Copy any BGRX/XRGB-formatted bytes-like buffer to the framebuffer
        in bulk row writes. Source layout is `src_w * src_h * 4` bytes
        (B, G, R, X per pixel) — the same order ramfb / bochs-VBE expect
        for XRGB8888 little-endian on both x86_64 and arm64.

        Used by Framebuffer.blit (kernel.display.Surface) and by the
        compositor (sdl2.SDL_Surface, which uses the same layout).
        """
        bpp = self._bytes_per_pixel
        # Vertical clip.
        sy0 = max(0, -dst_y)
        sy1 = min(src_h, self.height - dst_y)
        if sy1 <= sy0:
            return
        # Horizontal: bulk path requires dst_x >= 0. Fall back per-pixel
        # only for the rare off-screen-left case.
        if dst_x < 0:
            return self._blit_buffer_slow(buf, src_w, src_h, dst_x, dst_y)
        sx_count = min(src_w, self.width - dst_x)
        if sx_count <= 0:
            return
        row_bytes = sx_count * 4
        mv = memoryview(buf)
        for sy in range(sy0, sy1):
            dy = dst_y + sy
            src_off = sy * src_w * 4
            mmio_write_buf32(self.phys + dy * self.pitch + dst_x * bpp,
                              mv[src_off:src_off + row_bytes])

    def _blit_buffer_slow(self, buf, src_w: int, src_h: int,
                            dst_x: int, dst_y: int) -> None:
        """Per-pixel fallback for off-screen-left blits (rare path)."""
        bpp = self._bytes_per_pixel
        for sy in range(src_h):
            dy = dst_y + sy
            if dy < 0 or dy >= self.height:
                continue
            fb_row  = self.phys + dy * self.pitch
            src_off = sy * src_w * 4
            for sx in range(src_w):
                dx = dst_x + sx
                if dx < 0 or dx >= self.width:
                    continue
                so = src_off + sx * 4
                pixel = (
                    (buf[so + 2] << 16) |
                    (buf[so + 1] << 8)  |
                     buf[so]
                )
                mmio_write32(fb_row + dx * bpp, pixel)

    def draw_char(self, x: int, y: int, char: str,
                  fg: int = WHITE, bg: int | None = None) -> None:
        glyph = get_glyph(char)
        for row, byte in enumerate(glyph):
            for col in range(8):
                px = x + col
                py = y + row
                if byte & (0x80 >> col):
                    self.put_pixel(px, py, fg)
                elif bg is not None:
                    self.put_pixel(px, py, bg)

    def draw_text(self, x: int, y: int, text: str,
                  fg: int = WHITE, bg: int | None = None) -> tuple[int, int]:
        """Draw string; returns (x, y) position after last character."""
        cx, cy = x, y
        for ch in text:
            if ch == '\n':
                cy += GLYPH_H
                cx = x
            else:
                self.draw_char(cx, cy, ch, fg, bg)
                cx += GLYPH_W
        return cx, cy


# Module-level singleton — set by kernel.boot() when fb info is available
fb: Framebuffer | None = None

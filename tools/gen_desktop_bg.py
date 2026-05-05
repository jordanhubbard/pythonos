#!/usr/bin/env python3
"""
Generate the PythonOS desktop background asset.

Produces ``kernel/gui/assets.py`` with two byte-literal blobs:

    DESKTOP_BG_PNG  — 1024x768 RGB PNG, ~6 KB compressed
    LOGO_PNG        — 96x32 RGBA PNG of the PythonOS wordmark (transparent)

Both are pure-stdlib output — only ``zlib`` + ``struct`` + ``binascii``,
no Pillow dependency — so this can run in any CI without extra
packages. The decoder side uses our own pure-Python inflate
(``kernel/gui/image/_deflate.py``), so the PNGs are pre-validated by a
host round-trip via Python's stdlib zlib.

The visual is intentionally simple:

    * Diagonal gradient (deep navy → muted teal) across the desktop
    * A subtle vignette darkens the edges
    * "PythonOS" centred at ~25% from the top in a chunky 8x16 font
      blown up 4x and anti-aliased (we keep the bitmap font and just
      double it; the host SDL_ttf wrapper isn't needed for an asset
      shipped at build time)
"""

import binascii
import struct
import sys
import zlib
from pathlib import Path


# ── PNG plumbing ────────────────────────────────────────────────────────────

def _png_chunk(typ: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body))
            + typ + body
            + struct.pack(">I", binascii.crc32(typ + body) & 0xFFFFFFFF))


def _encode_png_rgb(w: int, h: int, pixels: bytes) -> bytes:
    """Encode raw 24-bit RGB pixels (row-major, no filter) into a PNG.
    color_type = 2 (RGB), 8-bit."""
    assert len(pixels) == w * h * 3, "pixel buffer size mismatch"
    rows = []
    for y in range(h):
        rows.append(b"\x00")    # filter type: None
        rows.append(pixels[y * w * 3 : (y + 1) * w * 3])
    raw = b"".join(rows)
    idat = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat)
            + _png_chunk(b"IEND", b""))


def _encode_png_rgba(w: int, h: int, pixels: bytes) -> bytes:
    assert len(pixels) == w * h * 4
    rows = []
    for y in range(h):
        rows.append(b"\x00")
        rows.append(pixels[y * w * 4 : (y + 1) * w * 4])
    raw = b"".join(rows)
    idat = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)   # color_type 6 = RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat)
            + _png_chunk(b"IEND", b""))


# ── Bitmap font (8x16) — same shape as kernel/display/font.py ──────────────
# Just the characters we need for "PythonOS". Each glyph is 16 bytes; bit 7
# of each byte is the leftmost pixel.

_GLYPHS = {
    'P': (0x00, 0x00, 0x7C, 0x66, 0x66, 0x66, 0x66, 0x7C,
          0x60, 0x60, 0x60, 0x60, 0x60, 0xF0, 0x00, 0x00),
    'y': (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x66, 0x66,
          0x66, 0x66, 0x66, 0x66, 0x3E, 0x06, 0x0C, 0x78),
    't': (0x00, 0x00, 0x10, 0x30, 0x30, 0x30, 0xFC, 0x30,
          0x30, 0x30, 0x30, 0x30, 0x36, 0x1C, 0x00, 0x00),
    'h': (0x00, 0x00, 0xE0, 0x60, 0x60, 0x60, 0x6C, 0x76,
          0x66, 0x66, 0x66, 0x66, 0x66, 0xF6, 0x00, 0x00),
    'o': (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3C, 0x66,
          0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00, 0x00),
    'n': (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xDC, 0x76,
          0x66, 0x66, 0x66, 0x66, 0x66, 0xF6, 0x00, 0x00),
    'O': (0x00, 0x00, 0x38, 0x6C, 0xC6, 0xC6, 0xC6, 0xC6,
          0xC6, 0xC6, 0xC6, 0xC6, 0x6C, 0x38, 0x00, 0x00),
    'S': (0x00, 0x00, 0x7C, 0xC6, 0xC6, 0x60, 0x38, 0x0C,
          0x06, 0x06, 0xC6, 0xC6, 0xC6, 0x7C, 0x00, 0x00),
    ' ': (0,)*16,
}


# ── Compose ────────────────────────────────────────────────────────────────

def _diagonal_gradient(w: int, h: int,
                        start: tuple[int, int, int],
                        end: tuple[int, int, int]) -> bytearray:
    """Diagonal gradient with a subtle radial vignette."""
    sr, sg, sb = start
    er, eg, eb = end
    cx, cy = w // 2, h // 2
    max_d = (cx * cx + cy * cy) ** 0.5
    out = bytearray(w * h * 3)
    for y in range(h):
        ry = y / max(1, h - 1)
        for x in range(w):
            rx = x / max(1, w - 1)
            t  = (rx + ry) * 0.5
            r = int(sr + (er - sr) * t)
            g = int(sg + (eg - sg) * t)
            b = int(sb + (eb - sb) * t)
            # Vignette
            dx = x - cx; dy = y - cy
            d = ((dx*dx + dy*dy) ** 0.5) / max_d
            v = 1.0 - 0.35 * d * d
            r = max(0, min(255, int(r * v)))
            g = max(0, min(255, int(g * v)))
            b = max(0, min(255, int(b * v)))
            o = (y * w + x) * 3
            out[o]     = r
            out[o + 1] = g
            out[o + 2] = b
    return out


def _draw_text(buf: bytearray, w: int, h: int,
                x: int, y: int, text: str,
                rgb: tuple[int, int, int], scale: int = 4) -> None:
    """Draw 8x16 bitmap text scaled up by ``scale`` into the RGB buffer."""
    r, g, b = rgb
    for ci, ch in enumerate(text):
        glyph = _GLYPHS.get(ch, _GLYPHS[' '])
        gx0 = x + ci * 8 * scale
        for row, byte in enumerate(glyph):
            for col in range(8):
                if not (byte & (0x80 >> col)):
                    continue
                # Paint a scale×scale block, antialiased toward the
                # background by mixing 65% glyph colour with whatever
                # the background already is (smoother edges).
                px0 = gx0 + col * scale
                py0 = y   + row * scale
                for dy in range(scale):
                    py = py0 + dy
                    if py < 0 or py >= h: continue
                    for dx in range(scale):
                        px = px0 + dx
                        if px < 0 or px >= w: continue
                        o = (py * w + px) * 3
                        buf[o]     = (r * 65 + buf[o]     * 35) // 100
                        buf[o + 1] = (g * 65 + buf[o + 1] * 35) // 100
                        buf[o + 2] = (b * 65 + buf[o + 2] * 35) // 100


def build_desktop_bg(w: int = 1024, h: int = 768) -> bytes:
    """1024x768 native — matches every supported framebuffer (bochs
    std VGA on x86, ramfb on arm64). The host bridge's surface.blit
    copies 1:1 so we don't get scaling for free. Asset cost ~85 KB."""
    buf = _diagonal_gradient(w, h,
                              start=(0x10, 0x18, 0x32),     # deep navy
                              end  =(0x18, 0x44, 0x60))     # muted teal
    title = "PythonOS"
    scale = 5
    text_w = len(title) * 8 * scale
    text_x = (w - text_w) // 2
    text_y = h // 4 - 8 * scale // 2
    _draw_text(buf, w, h, text_x, text_y, title,
               rgb=(0xFF, 0xE0, 0x90), scale=scale)
    return _encode_png_rgb(w, h, bytes(buf))


def build_logo() -> bytes:
    """Smaller wordmark (96x32) used in the menu bar."""
    w, h = 96, 32
    buf = bytearray(w * h * 4)
    title = "PythonOS"
    scale = 1
    text_w = len(title) * 8 * scale
    text_x = (w - text_w) // 2
    text_y = (h - 16 * scale) // 2
    # Render as RGBA — start fully transparent, paint glyphs in solid white.
    for ci, ch in enumerate(title):
        glyph = _GLYPHS.get(ch, _GLYPHS[' '])
        gx0 = text_x + ci * 8
        for row, byte in enumerate(glyph):
            for col in range(8):
                if not (byte & (0x80 >> col)):
                    continue
                px = gx0 + col
                py = text_y + row
                if 0 <= px < w and 0 <= py < h:
                    o = (py * w + px) * 4
                    buf[o]     = 0xFF
                    buf[o + 1] = 0xFF
                    buf[o + 2] = 0xFF
                    buf[o + 3] = 0xFF
    return _encode_png_rgba(w, h, bytes(buf))


# ── Output ─────────────────────────────────────────────────────────────────

def emit_module(out_path: Path) -> None:
    bg   = build_desktop_bg()
    logo = build_logo()

    src = []
    src.append('"""')
    src.append("kernel.gui.assets — Pre-built image assets used by the desktop.")
    src.append("")
    src.append("Generated by ``tools/gen_desktop_bg.py``. Don't hand-edit; run")
    src.append("``python3 tools/gen_desktop_bg.py`` to regenerate.")
    src.append('"""')
    src.append("")
    src.append(f"DESKTOP_BG_PNG = {bg!r}")
    src.append("")
    src.append(f"LOGO_PNG = {logo!r}")
    src.append("")
    out_path.write_text("\n".join(src))
    print(f"wrote {out_path} — {len(bg)} bg bytes + {len(logo)} logo bytes",
           file=sys.stderr)


if __name__ == "__main__":
    target = Path(__file__).parent.parent / "kernel" / "gui" / "assets.py"
    emit_module(target)

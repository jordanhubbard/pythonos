"""kernel.gui.image.bmp — Bytes-based 24/32-bit BMP decoder.

Mirrors the file-based ``SDL_LoadBMP`` in :mod:`sdl2.surface` but takes
raw bytes so the kernel can decode without going through libc's ``open()``
(which returns ENOSYS in our build).
"""

from kernel.gui.sdl2.surface import SDL_Surface


def decode_bmp(data: bytes) -> SDL_Surface:
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("bmp: not a BMP file")
    px_off = int.from_bytes(data[10:14], "little")
    width  = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    bpp    = int.from_bytes(data[28:30], "little")
    if bpp not in (24, 32):
        raise ValueError(f"bmp: unsupported bpp={bpp}")
    flip = height > 0
    h = abs(height)
    s = SDL_Surface(width, h, host_backed=False)
    row_bytes = (width * bpp // 8 + 3) & ~3
    for row in range(h):
        src_row = h - 1 - row if flip else row
        src_off = px_off + src_row * row_bytes
        dst_off = row * width * 4
        for x in range(width):
            so = src_off + x * (bpp // 8)
            b = data[so]
            g = data[so + 1]
            r = data[so + 2]
            s.pixels[dst_off + x * 4]     = b
            s.pixels[dst_off + x * 4 + 1] = g
            s.pixels[dst_off + x * 4 + 2] = r
            s.pixels[dst_off + x * 4 + 3] = 0xFF
    return s

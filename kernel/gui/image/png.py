"""kernel.gui.image.png — PNG decoder backed by the pure-Python deflate.

Supports the common PNG cases produced by everyday tools:

    Color type 2 (truecolor RGB), 8-bit
    Color type 6 (truecolor + alpha RGBA), 8-bit
    Color type 0 (grayscale), 8-bit
    Color type 4 (grayscale + alpha), 8-bit

Reads the IHDR + concatenated IDAT chunks, zlib-inflates the IDAT stream,
unfilters scanlines (None / Sub / Up / Average / Paeth) and converts the
result to XRGB8888 in an :class:`SDL_Surface`.

Indexed-color (type 3, palette) and bit depths other than 8 are not yet
supported — they raise :class:`NotImplementedError`. Most modern tools
default to 8-bit RGB(A) so that's the right v0 cut.
"""

from kernel.gui.sdl2.surface import SDL_Surface
from kernel.gui.image._deflate import zlib_inflate


PNG_SIG = b"\x89PNG\r\n\x1a\n"


# ── Chunk parsing ───────────────────────────────────────────────────────────

def _u32_be(data: bytes, off: int) -> int:
    return ((data[off] << 24) | (data[off + 1] << 16) |
            (data[off + 2] << 8) | data[off + 3])


def _read_chunks(data: bytes):
    """Yield (chunk_type: bytes, body: bytes) for every chunk in the PNG.

    The 4-byte CRC trailer is *not* checked (we trust the source); for
    PNGs produced inside the same VM by Pillow / pypng / imagemagick
    that's fine.
    """
    if not data.startswith(PNG_SIG):
        raise ValueError("png: missing PNG signature")
    off = 8
    while off + 12 <= len(data):
        length = _u32_be(data, off)
        ctype  = data[off + 4: off + 8]
        body   = data[off + 8: off + 8 + length]
        # +4 CRC at end of chunk
        off += 8 + length + 4
        yield ctype, body
        if ctype == b"IEND":
            return


# ── Filter unfiltering (RFC 2083 §6) ────────────────────────────────────────

def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc: return a
    if pb <= pc:              return b
    return c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    """Apply per-scanline reverse filter to ``raw`` IDAT bytes.

    ``bpp`` is bytes-per-pixel; rows are width*bpp bytes long preceded
    by a single filter-type byte. Result is height * width*bpp bytes
    of pixel data with no filter bytes.
    """
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    src_off = 0
    for y in range(height):
        ftype = raw[src_off]
        src_off += 1
        row_start = y * stride
        if ftype == 0:
            out[row_start:row_start + stride] = raw[src_off:src_off + stride]
        elif ftype == 1:  # Sub
            for x in range(stride):
                left = out[row_start + x - bpp] if x >= bpp else 0
                out[row_start + x] = (raw[src_off + x] + left) & 0xFF
        elif ftype == 2:  # Up
            for x in range(stride):
                out[row_start + x] = (raw[src_off + x] + prev[x]) & 0xFF
        elif ftype == 3:  # Average
            for x in range(stride):
                left = out[row_start + x - bpp] if x >= bpp else 0
                up   = prev[x]
                out[row_start + x] = (raw[src_off + x] + ((left + up) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for x in range(stride):
                left = out[row_start + x - bpp] if x >= bpp else 0
                up   = prev[x]
                ul   = prev[x - bpp] if x >= bpp else 0
                out[row_start + x] = (raw[src_off + x] + _paeth(left, up, ul)) & 0xFF
        else:
            raise ValueError(f"png: unknown filter type {ftype}")
        prev[:] = out[row_start:row_start + stride]
        src_off += stride
    return bytes(out)


# ── Public API ──────────────────────────────────────────────────────────────

def decode_png(data: bytes) -> SDL_Surface:
    """Decode a PNG byte string into an :class:`SDL_Surface`."""

    width = height = bit_depth = color_type = 0
    idat_parts: list[bytes] = []
    for ctype, body in _read_chunks(data):
        if ctype == b"IHDR":
            if len(body) < 13:
                raise ValueError("png: short IHDR")
            width      = _u32_be(body, 0)
            height     = _u32_be(body, 4)
            bit_depth  = body[8]
            color_type = body[9]
            compression= body[10]
            filter_meth= body[11]
            interlace  = body[12]
            if compression != 0:
                raise NotImplementedError("png: non-zero compression method")
            if filter_meth != 0:
                raise NotImplementedError("png: non-zero filter method")
            if interlace != 0:
                raise NotImplementedError("png: Adam7 interlacing not supported")
            if bit_depth != 8:
                raise NotImplementedError(f"png: bit depth {bit_depth} not supported")
            if color_type not in (0, 2, 4, 6):
                raise NotImplementedError(f"png: color type {color_type} not supported")
        elif ctype == b"IDAT":
            idat_parts.append(body)
        elif ctype == b"IEND":
            break
        # tRNS / PLTE / etc. ignored for v0 truecolor + grayscale.

    if width == 0 or height == 0:
        raise ValueError("png: missing IHDR")
    if not idat_parts:
        raise ValueError("png: missing IDAT")

    raw = zlib_inflate(b"".join(idat_parts))

    if color_type == 0:    bpp = 1   # grayscale
    elif color_type == 2:  bpp = 3   # RGB
    elif color_type == 4:  bpp = 2   # gray + alpha
    elif color_type == 6:  bpp = 4   # RGBA
    else:
        raise NotImplementedError(f"png: color type {color_type}")

    pixels = _unfilter(raw, width, height, bpp)

    s = SDL_Surface(width, height, host_backed=False)
    if color_type == 2:    # RGB
        for i in range(width * height):
            r = pixels[i*3]; g = pixels[i*3+1]; b = pixels[i*3+2]
            s.pixels[i*4]   = b
            s.pixels[i*4+1] = g
            s.pixels[i*4+2] = r
            s.pixels[i*4+3] = 0xFF
    elif color_type == 6:  # RGBA
        for i in range(width * height):
            r = pixels[i*4]; g = pixels[i*4+1]; b = pixels[i*4+2]
            s.pixels[i*4]   = b
            s.pixels[i*4+1] = g
            s.pixels[i*4+2] = r
            s.pixels[i*4+3] = pixels[i*4+3]
    elif color_type == 0:  # grayscale
        for i in range(width * height):
            v = pixels[i]
            s.pixels[i*4]   = v
            s.pixels[i*4+1] = v
            s.pixels[i*4+2] = v
            s.pixels[i*4+3] = 0xFF
    elif color_type == 4:  # grayscale + alpha
        for i in range(width * height):
            v = pixels[i*2]; a = pixels[i*2+1]
            s.pixels[i*4]   = v
            s.pixels[i*4+1] = v
            s.pixels[i*4+2] = v
            s.pixels[i*4+3] = a
    return s


def load_png(path: str) -> SDL_Surface:
    """File-based wrapper. Only works on hosts where Python's builtin
    ``open()`` is wired up (test harnesses); inside the bare-metal
    kernel the libc returns ENOSYS for arbitrary file paths, so the
    kernel goes through :func:`decode_png` with bytes from the VFS."""
    with open(path, "rb") as f:
        return decode_png(f.read())

"""kernel.gui.image.ppm — Netpbm P6 binary RGB decoder.

Format:
    P6
    <maxlines of '#' comments>
    <width> <height>
    <maxval>     # 0 < maxval < 65536; we only handle 8-bit (maxval <= 255)
    <width*height*3 raw bytes RGB>
"""

from kernel.gui.sdl2.surface import SDL_Surface


def _read_token(data: bytes, off: int) -> tuple[bytes, int]:
    """Skip whitespace + '# ...\\n' comments, then read one token."""
    n = len(data)
    while off < n:
        c = data[off:off+1]
        if c in (b" ", b"\n", b"\r", b"\t"):
            off += 1
        elif c == b"#":
            while off < n and data[off:off+1] != b"\n":
                off += 1
        else:
            break
    start = off
    while off < n and data[off:off+1] not in (b" ", b"\n", b"\r", b"\t"):
        off += 1
    return data[start:off], off


def decode_ppm(data: bytes) -> SDL_Surface:
    if not data.startswith(b"P6"):
        raise ValueError(f"PPM: not a P6 file")

    off = 2
    w_tok, off = _read_token(data, off)
    h_tok, off = _read_token(data, off)
    m_tok, off = _read_token(data, off)
    # Skip exactly one whitespace byte after the maxval (per Netpbm spec)
    if off < len(data) and data[off:off+1] in (b" ", b"\n", b"\r", b"\t"):
        off += 1

    width  = int(w_tok)
    height = int(h_tok)
    maxval = int(m_tok)
    if maxval > 255:
        raise ValueError(f"PPM: 16-bit (maxval={maxval}) not supported")

    s = SDL_Surface(width, height, host_backed=False)
    n_pixels = width * height
    body = data[off : off + n_pixels * 3]
    if len(body) < n_pixels * 3:
        raise ValueError(f"PPM: truncated pixel data")
    for i in range(n_pixels):
        r = body[i*3]
        g = body[i*3 + 1]
        b = body[i*3 + 2]
        o = i * 4
        s.pixels[o]     = b
        s.pixels[o + 1] = g
        s.pixels[o + 2] = r
        s.pixels[o + 3] = 0xFF
    return s


def load_ppm(path: str) -> SDL_Surface:
    """File-based wrapper. Use :func:`decode_ppm` inside the kernel."""
    with open(path, "rb") as f:
        return decode_ppm(f.read())

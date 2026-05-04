"""sdl2.surface — SDL_Surface and friends.

Surfaces have two backings:

* HOST-backed (default when the pythonos_bridge companion is up): the
  pixel buffer lives in the host process; `self.handle` is an integer
  the bridge uses to identify the SDL_Surface. SDL_FillRect /
  SDL_BlitSurface become bridge ops with NO pixel data on the wire.
  This is the fast path and what window surfaces use.

* GUEST-backed: classic mode — `self.pixels` is a guest bytearray and
  drawing mutates it directly. Used by image decoders (PNG/JPEG/BMP),
  TextWin scroll, and anything else that needs raw pixel access. Such
  a surface can be uploaded to a fresh host handle via
  `Surface.upload_to_host()` when it's time to display it.

The `pixels` attribute is None on host-backed surfaces — code that
needs to poke pixels must request guest backing explicitly.
"""

from dataclasses import dataclass


# ── PixelFormat (minimal) ───────────────────────────────────────────────────

class SDL_PixelFormat:
    """All we expose is BitsPerPixel — that's what SDL_MapRGB looks at."""
    def __init__(self, bpp: int = 32) -> None:
        self.BitsPerPixel = bpp
        self.format = 0x16462004  # SDL_PIXELFORMAT_XRGB8888 — informational

    @property
    def contents(self):  # PySDL2 ctypes-style accessor
        return self


# ── Geometry types ──────────────────────────────────────────────────────────

@dataclass
class SDL_Rect:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


@dataclass
class SDL_Point:
    x: int = 0
    y: int = 0


@dataclass
class SDL_Color:
    r: int = 0
    g: int = 0
    b: int = 0
    a: int = 255


# ── Bridge availability ─────────────────────────────────────────────────────

def _bridge_open() -> bool:
    """Was a hello() handshake successful? Cheap inline check —
    avoids a circular import at module load."""
    try:
        from kernel.bridge import bridge as _br
        return _br.opened
    except Exception:
        return False


# ── Surface ─────────────────────────────────────────────────────────────────

class SDL_Surface:
    """XRGB8888 surface — runs in one of three modes:

    * `host`     — pure host-backed. `pixels` is None; `handle` is a
                   bridge handle. SDL_FillRect / SDL_BlitSurface dispatch
                   as bridge ops with no pixel data on the wire.
    * `guest`    — pure guest-backed. `pixels` is a bytearray; `handle`
                   is 0. Drawing mutates the bytearray directly. Used by
                   image decoders that need raw pixel access.
    * `mirrored` — guest is the source of truth (so draw_char / pixel
                   pokes work), but the host has a handle the compositor
                   blits from. `_sync_to_host()` uploads when dirty.

    Construct with `host_backed=True/False` to force; `None` auto-picks
    `host` if the bridge is open, `guest` otherwise.
    """

    def __init__(self, w: int, h: int,
                 host_backed: bool | None = None) -> None:
        self.w     = w
        self.h     = h
        self.pitch = w * 4
        self.format = SDL_PixelFormat(32)

        if host_backed is None:
            host_backed = _bridge_open()
        self.host_backed = host_backed
        self.dirty = False    # guest pixels written since last host sync

        if host_backed:
            from kernel.bridge import bridge as _br
            r = _br.call("surface.create", {"w": w, "h": h})
            self.handle = int(r["handle"])
            self.pixels = None
        else:
            self.handle = 0   # lazy: allocated when first synced to host
            self.pixels = bytearray(w * h * 4)

    @property
    def contents(self):
        return self

    def _sync_to_host(self) -> int:
        """Return a host bridge handle the compositor can blit from.
        For `host` mode, returns `self.handle` directly. For `guest` /
        `mirrored` mode, lazily allocates a host handle and uploads
        pixels if `dirty`. Cleared dirty bit on success."""
        if self.host_backed:
            return self.handle
        if not _bridge_open():
            return 0
        from kernel.bridge import bridge as _br
        if self.handle == 0:
            r = _br.call("surface.create", {"w": self.w, "h": self.h})
            self.handle = int(r["handle"])
            self.dirty = True   # force initial upload
        if self.dirty:
            _br.call("surface.upload", {"handle": self.handle},
                     payload=bytes(self.pixels))
            self.dirty = False
        return self.handle

    # Backward-compat shim — old code path: just a sync.
    def _promote_to_host(self) -> None:
        self._sync_to_host()

    def free(self) -> None:
        if self.handle:
            try:
                from kernel.bridge import bridge as _br
                _br.call("surface.destroy", {"handle": self.handle})
            except Exception:
                pass
            self.handle = 0

    # ── Pixel access ───────────────────────────────────────────────────────
    # Guest-backed surfaces support direct pixel pokes. Host-backed
    # surfaces don't have `pixels`; callers should use SDL_FillRect /
    # SDL_BlitSurface (which dispatch through the bridge).

    def _put(self, x: int, y: int, color: int) -> None:
        if self.host_backed:
            return  # silent: host-backed surfaces don't expose pixel pokes
        if 0 <= x < self.w and 0 <= y < self.h:
            o = (y * self.w + x) * 4
            self.pixels[o]     =  color        & 0xFF  # B
            self.pixels[o + 1] = (color >>  8) & 0xFF  # G
            self.pixels[o + 2] = (color >> 16) & 0xFF  # R
            self.pixels[o + 3] = 0xFF                  # X
            self.dirty = True

    def _fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        if self.host_backed:
            from kernel.bridge import bridge as _br
            word = (color & 0xFFFFFF) | 0xFF000000
            _br.cast("surface.fill_rect", {
                "handle": self.handle,
                "rect": {"x": x, "y": y, "w": w, "h": h},
                "rgb": word,
            })
            return
        x1 = max(0, x); y1 = max(0, y)
        x2 = min(self.w, x + w); y2 = min(self.h, y + h)
        if x2 <= x1 or y2 <= y1:
            return
        from kernel.hal.io import buf_fill32_at
        word = (color & 0xFFFFFF) | 0xFF000000   # XRGB → BGRX little-endian
        span = x2 - x1
        for row in range(y1, y2):
            buf_fill32_at(self.pixels, (row * self.w + x1) * 4, span, word)
        self.dirty = True

    def _blit(self, src: "SDL_Surface", dst_x: int, dst_y: int,
              src_rect: SDL_Rect | None = None) -> None:
        if self.host_backed:
            src_handle = src._sync_to_host()
            from kernel.bridge import bridge as _br
            params = {
                "src": src_handle,
                "dst": self.handle,
                "dst_rect": {"x": dst_x, "y": dst_y, "w": src.w, "h": src.h},
            }
            if src_rect is not None:
                params["src_rect"] = {"x": src_rect.x, "y": src_rect.y,
                                       "w": src_rect.w, "h": src_rect.h}
            _br.cast("surface.blit", params)
            return
        # guest-backed dst.
        if src.host_backed:
            return  # no download path for host→guest blits in v0
        for sy in range(src.h):
            dy = dst_y + sy
            if dy < 0 or dy >= self.h:
                continue
            so = sy * src.w * 4
            do = (dy * self.w + dst_x) * 4
            n = min(src.w, self.w - dst_x) * 4
            if n <= 0:
                continue
            self.pixels[do:do + n] = src.pixels[so:so + n]
        self.dirty = True

    # ── Text rendering ──────────────────────────────────────────────────────
    # On host-backed surfaces, route through the bridge `text.draw` op.
    # On guest-backed surfaces, render via the existing pixel-poke path.

    def draw_char(self, x: int, y: int, char: str,
                  fg: int = 0xFFFFFF, bg: int | None = None) -> None:
        if self.host_backed:
            self.draw_text(x, y, char, fg=fg, bg=bg)
            return
        from kernel.display.font import get_glyph
        glyph = get_glyph(char)
        for row, byte in enumerate(glyph):
            for col in range(8):
                px = x + col
                py = y + row
                if byte & (0x80 >> col):
                    self._put(px, py, fg)
                elif bg != None:
                    self._put(px, py, bg)

    def draw_text(self, x: int, y: int, text: str,
                  fg: int = 0xFFFFFF, bg: int | None = None) -> tuple[int, int]:
        from kernel.display.font import GLYPH_W, GLYPH_H
        if self.host_backed:
            from kernel.bridge import bridge as _br
            params = {
                "handle": self.handle,
                "x": x, "y": y, "text": text,
                "fg": (fg & 0xFFFFFF) | 0xFF000000,
            }
            if bg is not None:
                params["bg"] = (bg & 0xFFFFFF) | 0xFF000000
            _br.cast("text.draw", params)
            # Best-effort cursor advance — assumes single-line text.
            return (x + len(text) * GLYPH_W, y)
        cx, cy = x, y
        for ch in text:
            if ch == '\n':
                cy += GLYPH_H
                cx = x
            else:
                self.draw_char(cx, cy, ch, fg, bg)
                cx += GLYPH_W
        return cx, cy


# ── Public API ──────────────────────────────────────────────────────────────

def SDL_MapRGB(fmt, r: int, g: int, b: int) -> int:
    """Pack an RGB triple to a 32-bit XRGB8888 pixel value."""
    return ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def SDL_MapRGBA(fmt, r: int, g: int, b: int, a: int) -> int:
    """For our XRGB surface alpha is ignored, but accepted for API parity."""
    return ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def SDL_FillRect(surface, rect, color: int) -> int:
    """Fill ``rect`` (or whole surface if rect == None) with ``color``."""
    s = surface.contents if hasattr(surface, "contents") else surface
    if rect == None:
        s._fill_rect(0, 0, s.w, s.h, color)
    else:
        r = rect.contents if hasattr(rect, "contents") else rect
        s._fill_rect(r.x, r.y, r.w, r.h, color)
    return 0


def SDL_FillRects(surface, rects, color: int) -> int:
    """Fill multiple rectangles with ``color`` in a single host call.

    ``rects`` is a sequence of ``SDL_Rect`` instances or
    ``(x, y, w, h)`` tuples. On host-backed surfaces this dispatches as
    one batched ``sdl.call('SDL_FillRects', ...)`` — one bridge round-trip
    no matter how many rects, which is the natural batching primitive
    for animations (starfield, particles, falling sand, …).
    """
    s = surface.contents if hasattr(surface, "contents") else surface
    # Normalize to a list of [x, y, w, h] arrays for compact JSON.
    flat: list = []
    for r in rects:
        if hasattr(r, "x"):
            flat.append([r.x, r.y, r.w, r.h])
        else:
            flat.append([int(r[0]), int(r[1]), int(r[2]), int(r[3])])
    if s.host_backed:
        from kernel.gui.sdl2.dispatch import sdl_cast
        word = (color & 0xFFFFFF) | 0xFF000000
        sdl_cast("SDL_FillRects", s.handle, flat, word)
        return 0
    # Guest-backed: just loop. Eventually a vectorized C primitive could
    # land in _hal but the pure-Python fallback is simple enough.
    for x, y, w, h in flat:
        s._fill_rect(x, y, w, h, color)
    return 0


def SDL_BlitSurface(src, src_rect, dst, dst_rect) -> int:
    s_src = src.contents if hasattr(src, "contents") else src
    s_dst = dst.contents if hasattr(dst, "contents") else dst
    dx = dst_rect.x if dst_rect != None else 0
    dy = dst_rect.y if dst_rect != None else 0
    sr = (src_rect.contents if hasattr(src_rect, "contents") else src_rect) \
         if src_rect is not None else None
    s_dst._blit(s_src, dx, dy, sr)
    return 0


def SDL_FreeSurface(surface) -> None:
    s = surface.contents if hasattr(surface, "contents") else surface
    s.free()


def SDL_LoadBMP(path: bytes | str):
    """Minimal BMP loader sufficient for the compatibility corpus.

    Always allocates a guest-backed surface (we need raw pixel access
    to decode), then leaves it for the caller to blit somewhere."""
    p = path.decode() if isinstance(path, (bytes, bytearray)) else str(path)
    with open(p, "rb") as f:
        data = f.read()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError(f"SDL_LoadBMP: {p}: not a BMP file")
    px_off = int.from_bytes(data[10:14], "little")
    width  = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    bpp    = int.from_bytes(data[28:30], "little")
    if bpp not in (24, 32):
        raise ValueError(f"SDL_LoadBMP: {p}: unsupported bpp={bpp}")
    flip = height > 0
    h = abs(height)
    s = SDL_Surface(width, h, host_backed=False)
    row_bytes = (width * bpp // 8 + 3) & ~3   # 4-byte aligned
    for row in range(h):
        src_row = h - 1 - row if flip else row
        sy = src_row
        src_off = px_off + sy * row_bytes
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

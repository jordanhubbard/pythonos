"""sdl2.sdlttf — anti-aliased text rendering via real SDL_ttf on the host.

Each function dispatches to libSDL2_ttf through the bridge's generic
``sdl.call`` op. The guest carries no font code of its own — opening
a font, rendering text, measuring text, all happen on the host.

Typical usage::

    from kernel.gui.sdl2.sdlttf import (
        TTF_Init, TTF_OpenFont, TTF_RenderUTF8_Blended,
        TTF_OpenDefaultFont, TTF_CloseFont,
    )
    from kernel.gui.sdl2.surface import SDL_BlitSurface, SDL_Rect

    TTF_Init()
    font = TTF_OpenDefaultFont(14)            # auto-discovers a system font
    label = TTF_RenderUTF8_Blended(font, "Hello", 0xFFFFFF)
    SDL_BlitSurface(label, None, win.surface, SDL_Rect(8, 8, label.w, label.h))
    label.free()

Compatibility shims for the older PySDL2-style calls are kept thin:

    TTF_RenderText_Blended  → forwards to TTF_RenderUTF8_Blended
    TTF_SizeText            → forwards to TTF_SizeUTF8
"""

from kernel.gui.sdl2.dispatch import sdl_call
from kernel.gui.sdl2.surface import SDL_Surface


# ── Font handle ─────────────────────────────────────────────────────────────

class TTF_Font:
    """Opaque host-side font handle. Closed via ``TTF_CloseFont`` or by
    its destructor; until then the pointer-equivalent ``handle`` is
    passed to render/size calls."""

    __slots__ = ("handle", "size", "path", "_closed")

    def __init__(self, handle: int, size: int, path: str = "") -> None:
        self.handle = handle
        self.size   = size
        self.path   = path
        self._closed = False

    def close(self) -> None:
        if self._closed or self.handle == 0:
            return
        try:
            sdl_call("TTF_CloseFont", self.handle)
        except Exception:
            pass
        self.handle = 0
        self._closed = True

    def __del__(self) -> None:
        self.close()


# ── Init / Quit ─────────────────────────────────────────────────────────────

def TTF_Init() -> int:
    """Initialise the host SDL_ttf library. Idempotent (libSDL2_ttf
    refcounts internally)."""
    return int(sdl_call("TTF_Init").get("rc", -1))


def TTF_Quit() -> None:
    sdl_call("TTF_Quit")


# ── Font open / close ───────────────────────────────────────────────────────

def TTF_OpenFont(path, size: int) -> TTF_Font:
    """Open a TrueType font at ``path`` with the given pixel size.
    Raises BridgeError if the file is missing or unreadable."""
    p = path.decode() if isinstance(path, (bytes, bytearray)) else str(path)
    r = sdl_call("TTF_OpenFont", p, int(size))
    return TTF_Font(int(r["handle"]), int(size), p)


def TTF_OpenDefaultFont(size: int) -> TTF_Font:
    """Open whatever monospace font the host can find. Convenience for
    apps that don't ship their own font assets — the host walks a small
    list of system paths and picks the first that exists."""
    r = sdl_call("pyo.default_font_path")
    return TTF_OpenFont(r["path"], size)


def TTF_CloseFont(font: TTF_Font) -> None:
    if font is not None:
        font.close()


# ── Rendering ───────────────────────────────────────────────────────────────

def _color_to_rgba(color) -> int:
    """Pack an RGB int / SDL_Color / (r,g,b[,a]) tuple as 0xRRGGBBAA."""
    if hasattr(color, "contents"):
        color = color.contents
    if hasattr(color, "r"):
        a = getattr(color, "a", 0xFF)
        return ((color.r & 0xFF) << 24) | ((color.g & 0xFF) << 16) \
             | ((color.b & 0xFF) <<  8) |  (a       & 0xFF)
    if isinstance(color, (tuple, list)):
        if len(color) >= 4:
            r, g, b, a = color[0], color[1], color[2], color[3]
        else:
            r, g, b = color[0], color[1], color[2]
            a = 0xFF
        return ((r & 0xFF) << 24) | ((g & 0xFF) << 16) \
             | ((b & 0xFF) <<  8) |  (a & 0xFF)
    if isinstance(color, int):
        # 0xRRGGBB convention; promote to 0xRRGGBBFF.
        return ((color & 0xFFFFFF) << 8) | 0xFF
    return 0xFFFFFFFF


def TTF_RenderUTF8_Blended(font: TTF_Font, text, fg) -> SDL_Surface:
    """Render UTF-8 ``text`` to a fresh ARGB8888 surface. The returned
    surface is host-backed and ready to blit; free it with ``.free()``
    (or by going out of scope) once it's been drawn."""
    s = text.decode() if isinstance(text, (bytes, bytearray)) else str(text)
    r = sdl_call("TTF_RenderUTF8_Blended", font.handle, s, _color_to_rgba(fg))
    surf = SDL_Surface.__new__(SDL_Surface)
    surf.w           = int(r["w"])
    surf.h           = int(r["h"])
    surf.pitch       = surf.w * 4
    surf.host_backed = True
    surf.handle      = int(r["handle"])
    surf.pixels      = None
    surf.dirty       = False
    from kernel.gui.sdl2.surface import SDL_PixelFormat
    surf.format = SDL_PixelFormat(32)
    return surf


def TTF_SizeUTF8(font: TTF_Font, text) -> tuple[int, int]:
    """Return ``(w, h)`` of the rendered text without producing a
    surface — useful for layout measurement."""
    s = text.decode() if isinstance(text, (bytes, bytearray)) else str(text)
    r = sdl_call("TTF_SizeUTF8", font.handle, s)
    return (int(r["w"]), int(r["h"]))


# ── Backward-compat aliases ─────────────────────────────────────────────────
# The "Text" variants are ASCII-only in classic SDL_ttf; UTF-8 is the
# strict superset, so we forward both.

def TTF_RenderText_Blended(font, text, fg):
    return TTF_RenderUTF8_Blended(font, text, fg)


def TTF_RenderText_Solid(font, text, fg):
    # Solid is non-anti-aliased; for our purposes Blended is fine.
    return TTF_RenderUTF8_Blended(font, text, fg)


def TTF_SizeText(font, text) -> tuple[int, int]:
    return TTF_SizeUTF8(font, text)

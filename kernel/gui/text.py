"""kernel.gui.text — Anti-aliased text helper for compositor chrome.

A thin singleton that hides the SDL_ttf dance for every GUI consumer
that wants real text instead of the 8×8 bitmap font:

    text_renderer.draw(surface, x, y, "Editor: foo.txt", 0xFFFFFF, size=11)
    w, h = text_renderer.measure("Hello", size=11)

Caches:
  * one TTF font handle per pt size
  * one SDL_Surface per (text, color, size) — host-backed, blitted via
    the existing sdl.call("SDL_BlitSurface", ...) path

Falls back to the legacy bitmap path (``surface.draw_text``) when
SDL_ttf can't load a default font (no system fonts on the host,
headless boot, bridge unavailable). The bitmap path uses fixed
8-pixel-wide glyph metrics regardless of the requested ``size``.

The cache lives for the lifetime of the GUI session and grows with the
set of distinct strings drawn — that's fine for chrome (titles, dock
labels, menu items) which is small and bounded; consumers that draw
unbounded streams of unique text (terminal output, editor body) should
keep using the bitmap path."""

import kernel.log as log

from kernel.display.font import GLYPH_W, GLYPH_H


class TextRenderer:
    def __init__(self) -> None:
        # pt size → TTF_Font handle, or False if a previous attempt failed.
        self._fonts: dict = {}
        # (text, color, size) → host-backed SDL_Surface.
        self._cache: dict = {}

    def _font(self, size: int):
        f = self._fonts.get(size)
        if f is not None:
            return f
        try:
            from kernel.gui.sdl2.sdlttf import (
                TTF_Init, TTF_OpenDefaultFont,
            )
            TTF_Init()
            f = TTF_OpenDefaultFont(size)
        except Exception as e:
            log.info(f"gui.text: SDL_ttf unavailable at {size}pt ({e})")
            f = False
        self._fonts[size] = f
        return f

    def _surface(self, text: str, color: int, size: int):
        font = self._font(size)
        if not font:
            return None
        key = (text, color, size)
        s = self._cache.get(key)
        if s is not None:
            return s
        try:
            from kernel.gui.sdl2.sdlttf import TTF_RenderUTF8_Blended
            rgba = ((color & 0xFFFFFF) << 8) | 0xFF
            s = TTF_RenderUTF8_Blended(font, text, rgba)
        except Exception as e:
            log.warn(f"gui.text: render({text!r}, {size}pt) failed: {e}")
            return None
        self._cache[key] = s
        return s

    def measure(self, text: str, size: int = 12) -> tuple[int, int]:
        """Return ``(w, h)`` of ``text`` rendered at ``size`` pt."""
        s = self._surface(text, 0xFFFFFF, size)
        if s is not None:
            return (s.w, s.h)
        return (max(1, len(text)) * GLYPH_W, GLYPH_H)

    def draw(self, surface, x: int, y: int, text: str,
             color: int, size: int = 12, bg: "int | None" = None
             ) -> tuple[int, int]:
        """Draw ``text`` on ``surface`` at ``(x, y)``. Returns ``(w, h)``
        of the rendered glyph run. ``bg`` only affects the bitmap
        fallback — TTF surfaces blend with whatever's already there."""
        s = self._surface(text, color, size)
        if s is not None:
            from kernel.gui.sdl2.surface import SDL_BlitSurface, SDL_Rect
            SDL_BlitSurface(s, None, surface, SDL_Rect(x, y, s.w, s.h))
            return (s.w, s.h)
        # Bitmap fallback.
        surface.draw_text(x, y, text, fg=color, bg=bg)
        return (max(1, len(text)) * GLYPH_W, GLYPH_H)

    def truncate_to_width(self, text: str, max_w: int,
                           size: int = 12) -> str:
        """Trim ``text`` from the right until its rendered width fits
        within ``max_w`` pixels. Caches each prefix it measures, which
        is cheap because successive measurements share most of the
        string (and the cache is keyed by text)."""
        if not text:
            return text
        s = self._surface(text, 0xFFFFFF, size)
        if s is None:
            # Bitmap fallback — fixed pitch.
            return text[: max(0, max_w // GLYPH_W)]
        if s.w <= max_w:
            return text
        # Binary-shrink by character count. TTF widths are monotonic in
        # length so the longest prefix that fits is unique.
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            ms = self._surface(text[:mid], 0xFFFFFF, size)
            if ms is not None and ms.w <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo]


# Module-level singleton so callers don't construct one per frame.
text_renderer = TextRenderer()

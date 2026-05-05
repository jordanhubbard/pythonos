"""apps._icons — Helpers for generating 48x48 dock icons procedurally.

Each app registers its `make_icon` callable with apps.registry. The
compositor calls it lazily once the bridge is up, so the resulting
SDL_Surface is host-backed and the per-frame dock blits are pure
handle-handle bridge ops with zero pixel data on the wire.
"""

from kernel.gui.sdl2.surface import SDL_Surface, SDL_FillRect, SDL_Rect


ICON_SIZE = 48


def _new_icon(bg: int) -> SDL_Surface:
    """Create a 48x48 surface filled with `bg`."""
    s = SDL_Surface(ICON_SIZE, ICON_SIZE)
    SDL_FillRect(s, None, bg)
    return s


def _border(s: SDL_Surface, color: int, thickness: int = 1) -> None:
    """Draw a 1-pixel border around an icon for definition."""
    SDL_FillRect(s, SDL_Rect(0, 0, ICON_SIZE, thickness), color)
    SDL_FillRect(s, SDL_Rect(0, ICON_SIZE - thickness, ICON_SIZE, thickness), color)
    SDL_FillRect(s, SDL_Rect(0, 0, thickness, ICON_SIZE), color)
    SDL_FillRect(s, SDL_Rect(ICON_SIZE - thickness, 0, thickness, ICON_SIZE), color)


def default_icon(name: str) -> SDL_Surface:
    """Fallback: gray square with the app name's first letter."""
    s = _new_icon(0x303040)
    _border(s, 0x808090)
    if name:
        s.draw_text(20, 20, name[0].upper(), fg=0xFFFFFF, bg=0x303040)
    return s


# ── Per-app icons ──────────────────────────────────────────────────────────

def bouncing_ball_icon() -> SDL_Surface:
    """Yellow square on a navy field (the literal demo)."""
    s = _new_icon(0x1A2A6E)
    _border(s, 0x4060B0)
    SDL_FillRect(s, SDL_Rect(16, 16, 16, 16), 0xFFCC00)
    return s


def terminal_icon() -> SDL_Surface:
    """Black field with a green prompt."""
    s = _new_icon(0x101010)
    _border(s, 0x60FF60)
    s.draw_text(8, 18, ">_", fg=0x60FF60, bg=0x101010)
    return s


def editor_icon() -> SDL_Surface:
    """Light page with three text rules."""
    s = _new_icon(0xE0E0E8)
    _border(s, 0x404048)
    SDL_FillRect(s, SDL_Rect(8, 12, 32, 2), 0x303038)
    SDL_FillRect(s, SDL_Rect(8, 22, 32, 2), 0x303038)
    SDL_FillRect(s, SDL_Rect(8, 32, 20, 2), 0x303038)
    return s


def files_icon() -> SDL_Surface:
    """Folder: tab on top, body below."""
    s = _new_icon(0x14182A)
    # tab
    SDL_FillRect(s, SDL_Rect(8, 12, 14, 6), 0xE0B040)
    # body
    SDL_FillRect(s, SDL_Rect(6, 16, 36, 24), 0xF0C050)
    _border(s, 0x804020)
    return s


def image_viewer_icon() -> SDL_Surface:
    """Frame with a sky/sun motif."""
    s = _new_icon(0x402060)
    _border(s, 0x80408C)
    # frame background
    SDL_FillRect(s, SDL_Rect(8, 8, 32, 32), 0xC0C0E0)
    # "sun" — a yellow square in the corner
    SDL_FillRect(s, SDL_Rect(28, 12, 8, 8), 0xFFD040)
    # "ground" — green band
    SDL_FillRect(s, SDL_Rect(8, 30, 32, 10), 0x40A040)
    return s


def audio_tone_icon() -> SDL_Surface:
    """Speaker glyph — cone + waves."""
    s = _new_icon(0x202030)
    _border(s, 0x8090A0)
    # speaker cone (left)
    SDL_FillRect(s, SDL_Rect(10, 18, 6, 12), 0xC0C0D0)
    # speaker face
    SDL_FillRect(s, SDL_Rect(16, 14, 4, 20), 0xC0C0D0)
    # sound waves (vertical bars increasing in height to the right)
    SDL_FillRect(s, SDL_Rect(26, 22, 2, 4),  0x60D0FF)
    SDL_FillRect(s, SDL_Rect(30, 19, 2, 10), 0x60D0FF)
    SDL_FillRect(s, SDL_Rect(34, 16, 2, 16), 0x60D0FF)
    return s


def paint_icon() -> SDL_Surface:
    """Paint palette: white canvas with three color dabs."""
    s = _new_icon(0xF0F0F0)
    _border(s, 0x404040)
    SDL_FillRect(s, SDL_Rect(10, 12, 8, 8), 0xE03020)   # red
    SDL_FillRect(s, SDL_Rect(20, 12, 8, 8), 0x30B030)   # green
    SDL_FillRect(s, SDL_Rect(30, 12, 8, 8), 0x3050E0)   # blue
    # brush stroke
    SDL_FillRect(s, SDL_Rect(8, 28, 32, 3), 0x101820)
    SDL_FillRect(s, SDL_Rect(12, 31, 24, 2), 0x101820)
    return s


def keyboard_demo_icon() -> SDL_Surface:
    """Stylized keyboard: 3 rows of small key rects."""
    s = _new_icon(0x202030)
    _border(s, 0x8090A0)
    key = 0xD0D0D8
    for row in range(3):
        y = 14 + row * 8
        for col in range(5):
            x = 6 + col * 8
            SDL_FillRect(s, SDL_Rect(x, y, 6, 6), key)
    # spacebar
    SDL_FillRect(s, SDL_Rect(10, 38, 28, 4), key)
    return s

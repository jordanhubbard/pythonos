"""apps._icons — Helpers for generating 48x48 dock icons procedurally.

Each app registers its `make_icon` callable with apps.registry. Icons
are guest-backed 48×48 surfaces so the chipset Workbench path can blit
pixels onto playfields; bridge compose uploads them via `_sync_to_host`.
"""

from kernel.gui.sdl2.surface import SDL_Surface, SDL_FillRect, SDL_Rect


ICON_SIZE = 48


def _new_icon(bg: int) -> SDL_Surface:
    """Create a 48x48 surface filled with `bg`.

    Guest-backed so the chipset Workbench path can blit icon pixels
    onto playfields. Bridge compose still uploads via ``_sync_to_host``.
    """
    s = SDL_Surface(ICON_SIZE, ICON_SIZE, host_backed=False)
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


def toaster_icon() -> SDL_Surface:
    """Color bars — Video Toaster program monitor."""
    s = _new_icon(0x101018)
    bars = (0xC00000, 0xC0C000, 0x00C000, 0x00C0C0, 0x0000C0, 0xC000C0)
    band = ICON_SIZE // len(bars)
    for i, color in enumerate(bars):
        SDL_FillRect(s, SDL_Rect(i * band, 8, band, 32), color)
    _border(s, 0xE0E0E0)
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


def defender_icon() -> SDL_Surface:
    """Side-view ship over a green hill."""
    s = _new_icon(0x081028)
    SDL_FillRect(s, SDL_Rect(0, 32, 48, 16), 0x406030)
    SDL_FillRect(s, SDL_Rect(10, 18, 28, 8), 0xE8E8F0)
    SDL_FillRect(s, SDL_Rect(30, 20, 10, 4), 0xE8E8F0)
    _border(s, 0x6080A0)
    return s


def pacmaze_icon() -> SDL_Surface:
    """Yellow pac against a blue maze wall."""
    s = _new_icon(0x000010)
    SDL_FillRect(s, SDL_Rect(4, 4, 40, 4), 0x2030C0)
    SDL_FillRect(s, SDL_Rect(4, 4, 4, 40), 0x2030C0)
    SDL_FillRect(s, SDL_Rect(16, 16, 16, 16), 0xFFD0A0)
    SDL_FillRect(s, SDL_Rect(28, 20, 8, 8), 0x000010)
    SDL_FillRect(s, SDL_Rect(36, 22, 4, 4), 0xFFD0A0)
    _border(s, 0x4060C0)
    return s


def raiders_icon() -> SDL_Surface:
    """Three green invaders over a yellow gun."""
    s = _new_icon(0x040818)
    SDL_FillRect(s, SDL_Rect(8, 8, 8, 8), 0x40FF80)
    SDL_FillRect(s, SDL_Rect(20, 8, 8, 8), 0x40FF80)
    SDL_FillRect(s, SDL_Rect(32, 8, 8, 8), 0x40FF80)
    SDL_FillRect(s, SDL_Rect(20, 32, 8, 8), 0xFFE060)
    _border(s, 0x306050)
    return s

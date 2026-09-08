"""Raster: composite the active View into a BGRX dest buffer."""

from kernel.chipset.playfield import MODE_INDEXED, pack_bgrx


BPLCON_PF1_KEY = 1


def _palette(view, index: int) -> int:
    if 0 <= index < 32:
        return view.palette[index] & 0xFFFFFF
    return 0


def _fill_dest(dest: bytearray, color: int) -> None:
    pix = pack_bgrx(color)
    n = len(dest) // 4
    dest[:] = pix * n


def _put_dest(dest: bytearray, dest_w: int, dest_h: int,
              x: int, y: int, color: int) -> None:
    if x < 0 or y < 0 or x >= dest_w or y >= dest_h:
        return
    o = (y * dest_w + x) * 4
    dest[o:o + 4] = pack_bgrx(color)


def _put_block(dest, dest_w, dest_h, x, y, scale, color) -> None:
    pix = pack_bgrx(color)
    for j in range(scale):
        dy = y + j
        if dy < 0 or dy >= dest_h:
            continue
        row = dy * dest_w
        for i in range(scale):
            dx = x + i
            if 0 <= dx < dest_w:
                o = (row + dx) * 4
                dest[o:o + 4] = pix


def _sample(view, px: int, py: int) -> int:
    pf0 = view.pf0
    c0 = pf0.sample(px, py)
    if pf0.mode == MODE_INDEXED:
        color = _palette(view, c0)
    else:
        color = c0

    pf1 = view.pf1
    if view.bplcon & BPLCON_PF1_KEY:
        c1 = pf1.sample(px, py)
        keyed = False
        if pf1.mode == MODE_INDEXED:
            keyed = c1 == (view.key_color & 0xFF)
            if not keyed:
                color = _palette(view, c1)
        else:
            keyed = c1 == (view.key_color & 0xFFFFFF)
            if not keyed:
                color = c1
    elif pf1.mode == MODE_INDEXED:
        c1 = pf1.sample(px, py)
        if c1 != 0:
            color = _palette(view, c1)
    else:
        # DIRECT PF1 with no key: still draw non-zero pixels so a
        # cleared-to-black PF1 does not wipe PF0.
        c1 = pf1.sample(px, py)
        if c1 != 0:
            color = c1

    palette = view.palette if view.mode == MODE_INDEXED else None
    for spr in view.sprites:
        sc = spr.pixel_at(px, py, palette)
        if sc is not None:
            color = sc
            break
    return color


def raster_view(view, dest: bytearray, dest_w: int, dest_h: int) -> None:
    """Composite ``view`` into ``dest`` (BGRX, dest_w*dest_h*4 bytes)."""
    if view is None:
        _fill_dest(dest, 0)
        return
    scale = max(1, int(view.scale))
    sprites_on = any(s.enabled for s in view.sprites)
    copper_on = bool(view.copper.instructions)
    fast = (
        view.mode != MODE_INDEXED
        and scale == 1
        and dest_w == view.width
        and dest_h == view.height
        and not sprites_on
        and not copper_on
        and view.bplcon == 0
        and view.diw_start <= 0
        and view.diw_stop >= view.height - 1
        and len(view.pf0.pixels) == len(dest)
    )
    if fast:
        dest[:] = view.pf0.pixels
        return
    _fill_dest(dest, 0)
    out_w = view.width * scale
    out_h = view.height * scale
    ox = (dest_w - out_w) // 2
    oy = (dest_h - out_h) // 2
    view.copper.reset_pc()
    diw0 = view.diw_start
    diw1 = view.diw_stop
    if diw1 < 0:
        diw1 = view.height - 1
    for py in range(view.height):
        view.copper.apply_line(view, py)
        if py < diw0 or py > diw1:
            continue
        for px in range(view.width):
            color = _sample(view, px, py)
            _put_block(dest, dest_w, dest_h,
                       ox + px * scale, oy + py * scale, scale, color)

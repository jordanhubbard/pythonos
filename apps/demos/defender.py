"""apps.demos.defender — scrolling terrain, landers, Paula thrust."""

from __future__ import annotations

from kernel.chipset import MODE_INDEXED, Move, View, Wait, blitter, paula
from kernel.gui import input as _gui_input
from apps import registry
from apps.arcade_logic import aabb, art_from_rows, mountain_height, scroll_wrap, square_pcm
from apps.chipset_play import run_view


SHIP = art_from_rows((
    "00022000",
    "00222200",
    "22222222",
    "02222220",
    "00222200",
    "00022000",
    "00000000",
    "00000000",
))
LANDER = art_from_rows((
    "00300300",
    "03333330",
    "00333300",
    "00033000",
    "00333300",
    "03000030",
    "00000000",
    "00000000",
))
HUMAN = art_from_rows((
    "00044000",
    "00044000",
    "00444400",
    "00044000",
    "00044000",
    "00400400",
    "00000000",
    "00000000",
))


def _paint_world(view: View) -> None:
    view.pf0.fill(0)
    for x in range(view.width):
        h = mountain_height(x, view.width)
        y0 = view.height - h
        blitter.fill(view.pf0, x, y0, 1, h, 1)
    view.pf1.fill(0)
    for i in range(24):
        view.pf1.put((i * 37) % view.width, 10 + (i * 13) % 80, 6)
    view.bplcon = 1
    view.key_color = 0


async def main(*args, **kwargs) -> None:
    v = View(320, 200, mode=MODE_INDEXED, scale=3)
    v.palette[0] = 0x081028
    v.palette[1] = 0x406030
    v.palette[2] = 0xE8E8F0
    v.palette[3] = 0xFF4060
    v.palette[4] = 0xFFCC60
    v.palette[5] = 0xFFFFFF
    v.palette[6] = 0xA0C0FF
    v.copper.instructions = [
        Wait(0), Move("COLOR00", 0x081028),
        Wait(50), Move("COLOR00", 0x102050),
        Wait(140), Move("COLOR00", 0x201018),
    ]
    _paint_world(v)
    ship = v.sprites[0]
    ship.place(SHIP, 8, 8, x=72, y=90, key_color=0)
    landers = [
        {"x": 160, "y": 20, "spr": v.sprites[1]},
        {"x": 260, "y": 8, "spr": v.sprites[2]},
    ]
    humans = [
        {"x": 140, "y": 168, "spr": v.sprites[3], "alive": True},
        {"x": 240, "y": 172, "spr": v.sprites[4], "alive": True},
    ]
    for i, L in enumerate(landers):
        L["spr"].place(LANDER, 8, 8, x=0, y=L["y"], key_color=0)
    for H in humans:
        H["spr"].place(HUMAN, 8, 8, x=0, y=H["y"], key_color=0)
    shot = v.sprites[5]
    shot.enabled = False

    thrust = paula.channel[0]
    thrust.sample = square_pcm(90, 200)
    thrust.rate = 8000
    thrust.volume = 18
    thrust.pan = 128
    thrust.loop = True
    thrust.loop_end = len(thrust.sample) // 2
    zap = paula.channel[1]
    zap.sample = square_pcm(1200, 60)
    zap.rate = 8000
    zap.volume = 32
    zap.pan = 180
    zap.loop = False

    def world_to_screen(wx: int) -> int:
        return scroll_wrap(wx - v.pf0.scroll_x, 0, v.width)

    def fire():
        if not shot.enabled:
            shot.place(bytes([5, 5, 5, 5]), 4, 1,
                       x=ship.x + 8, y=ship.y + 3, key_color=0)
            zap.stop()
            zap.play()

    def tick(keys):
        dx = 0
        if _gui_input.KEY_LEFT in keys:
            dx = -3
        if _gui_input.KEY_RIGHT in keys:
            dx = 3
        if dx:
            v.pf0.scroll_x = scroll_wrap(v.pf0.scroll_x, dx, v.width)
            v.pf1.scroll_x = scroll_wrap(v.pf1.scroll_x, dx // 2, v.width)
            if not thrust.playing:
                thrust.play()
        else:
            thrust.stop()
        if _gui_input.KEY_UP in keys:
            ship.y = max(16, ship.y - 3)
        if _gui_input.KEY_DOWN in keys:
            ship.y = min(160, ship.y + 3)
        if shot.enabled:
            shot.x += 8
            if shot.x > 318:
                shot.enabled = False
        for L in landers:
            target = None
            for H in humans:
                if H["alive"]:
                    target = H
                    break
            if target is not None:
                if L["x"] < target["x"]:
                    L["x"] += 1
                elif L["x"] > target["x"]:
                    L["x"] -= 1
                ground = 200 - mountain_height(L["x"], 320) - 8
                if L["y"] < ground:
                    L["y"] += 1
                elif aabb(L["x"], L["y"], 8, 8, target["x"], target["y"], 8, 8):
                    target["alive"] = False
                    target["spr"].enabled = False
                    L["y"] = max(8, L["y"] - 2)
            sx = world_to_screen(L["x"])
            L["spr"].x = sx
            L["spr"].y = L["y"]
            if shot.enabled and aabb(shot.x, shot.y, 4, 1, sx, L["y"], 8, 8):
                L["y"] = 8
                L["x"] = scroll_wrap(L["x"], 80, 320)
                shot.enabled = False
            if aabb(ship.x, ship.y, 8, 8, sx, L["y"], 8, 8):
                v.copper.instructions = [
                    Wait(0), Move("COLOR00", 0x401010),
                ]
        for H in humans:
            if H["alive"]:
                H["spr"].x = world_to_screen(H["x"])
                H["spr"].y = H["y"]

    def on_exit():
        thrust.stop()
        zap.stop()

    await run_view(v, tick, on_space=fire, on_exit=on_exit)


from apps._icons import defender_icon

registry.register(
    name="defender",
    description="Defender — scrolling hills, landers, Paula thrust",
    entry=main,
    icon_factory=defender_icon,
    category="game",
)

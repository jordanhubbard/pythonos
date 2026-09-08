"""apps.demos.raiders — Galaxian-style formation + dive."""

from __future__ import annotations

from kernel.chipset import MODE_INDEXED, Move, View, Wait, paula
from kernel.gui import input as _gui_input
from apps import registry
from apps.arcade_logic import aabb, art_from_rows, formation_xy, square_pcm
from apps.chipset_play import run_view


PLAYER = art_from_rows((
    "00022000",
    "00222200",
    "02222220",
    "22222222",
    "00222200",
    "02000020",
    "00000000",
    "00000000",
))
ALIEN = art_from_rows((
    "03000030",
    "00333300",
    "03333330",
    "33033033",
    "33333333",
    "00300300",
    "03000030",
    "00000000",
))


async def main(*args, **kwargs) -> None:
    v = View(320, 200, mode=MODE_INDEXED, scale=3)
    v.palette[0] = 0x040818
    v.palette[1] = 0x204060
    v.palette[2] = 0xFFE060
    v.palette[3] = 0x40FF80
    v.palette[5] = 0xFFFFFF
    v.copper.instructions = [
        Wait(0), Move("COLOR00", 0x040818),
        Wait(30), Move("COLOR00", 0x081030),
        Wait(160), Move("COLOR00", 0x180818),
    ]
    v.pf0.fill(0)
    player = v.sprites[0]
    player.place(PLAYER, 8, 8, x=156, y=176, key_color=0)
    aliens = []
    for i in range(4):
        spr = v.sprites[1 + i]
        x, y = formation_xy(i, 0, 80, 24)
        spr.place(ALIEN, 8, 8, x=x, y=y, key_color=0)
        aliens.append({"spr": spr, "alive": True, "dive": 0, "slot": i})
    shot = v.sprites[5]
    shot.enabled = False
    t = 0
    bass = paula.channel[0]
    bass.sample = square_pcm(110, 300)
    bass.rate = 8000
    bass.volume = 16
    bass.pan = 128
    bass.loop = True
    bass.loop_end = len(bass.sample) // 2
    bass.play()
    zap = paula.channel[1]
    zap.sample = square_pcm(880, 50)
    zap.rate = 8000
    zap.volume = 28
    zap.pan = 128
    zap.loop = False

    def fire():
        if not shot.enabled:
            shot.place(bytes([5, 5, 5, 5]), 1, 4,
                       x=player.x + 3, y=player.y - 6, key_color=0)
            zap.stop()
            zap.play()

    def tick(keys):
        nonlocal t
        t += 1
        if _gui_input.KEY_LEFT in keys:
            player.x = max(8, player.x - 4)
        if _gui_input.KEY_RIGHT in keys:
            player.x = min(304, player.x + 4)
        if shot.enabled:
            shot.y -= 8
            if shot.y < 0:
                shot.enabled = False
        diver = (t // 90) % 4
        alive_n = 0
        for a in aliens:
            if not a["alive"]:
                a["spr"].enabled = False
                continue
            alive_n += 1
            if a["slot"] == diver:
                a["dive"] = min(140, a["dive"] + 2)
            else:
                a["dive"] = max(0, a["dive"] - 1)
            fx, fy = formation_xy(a["slot"], t, 80, 24)
            a["spr"].x = fx
            a["spr"].y = fy + a["dive"]
            if shot.enabled and aabb(shot.x, shot.y, 1, 4,
                                     a["spr"].x, a["spr"].y, 8, 8):
                a["alive"] = False
                a["spr"].enabled = False
                shot.enabled = False
            if aabb(player.x, player.y, 8, 8, a["spr"].x, a["spr"].y, 8, 8):
                v.copper.instructions = [Wait(0), Move("COLOR00", 0x401010)]
        if alive_n == 0:
            for a in aliens:
                a["alive"] = True
                a["dive"] = 0
                a["spr"].enabled = True
            v.copper.instructions = [
                Wait(0), Move("COLOR00", 0x104010),
                Wait(30), Move("COLOR00", 0x081030),
            ]

    def on_exit():
        bass.stop()
        zap.stop()

    await run_view(v, tick, on_space=fire, on_exit=on_exit)


from apps._icons import raiders_icon

registry.register(
    name="raiders",
    description="Raiders — Galaxian-style formation and dive",
    entry=main,
    icon_factory=raiders_icon,
    category="game",
)

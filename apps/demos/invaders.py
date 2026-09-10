"""Alien Invaders — fixed-screen arcade game using the whole chipset.

Exercises both indexed playfields, dual-playfield keying, Copper palette and
display-window changes, fill/copy/cookie blits, all eight hardware sprites,
collision-driven shield damage, and all four Paula channels.
"""

from __future__ import annotations

from kernel.chipset import MODE_INDEXED, Move, Playfield, View, Wait, blitter, paula
from kernel.gui import input as _gui_input
from apps import registry
from apps.arcade_logic import aabb, art_from_rows, invader_formation_position, square_pcm
from apps.chipset_play import run_view

W, H = 320, 200
PLAYER_Y = 176
ALIEN_COLS, ALIEN_ROWS = 11, 5

PLAYER = art_from_rows(("00002000", "00022200", "00022200", "02222220",
                        "22222222", "22222222", "22000022", "00000000"))
ALIEN_A = art_from_rows(("00300300", "00033000", "00333300", "03303330",
                         "33333333", "30333303", "30300303", "00000000"))
ALIEN_B = art_from_rows(("00300300", "30033003", "30333303", "33303333",
                         "33333333", "00333300", "03000030", "30000003"))
UFO = art_from_rows(("000440000000", "004444400000", "044444440000",
                     "440440440000", "444444444000", "004400440000",
                     "000000000000", "000000000000"))
BURST = art_from_rows(("50000005", "05055050", "00555500", "05500550",
                       "05500550", "00555500", "05055050", "50000005"))
SHOT = bytes((6, 6, 6, 6, 6, 6))

FONT = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "111", "100", "111"), "3": ("110", "001", "111", "001", "110"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "110"),
    "A": ("010", "101", "111", "101", "101"), "C": ("111", "100", "100", "100", "111"),
    "E": ("111", "100", "110", "100", "111"), "G": ("111", "100", "101", "101", "111"),
    "H": ("101", "101", "111", "101", "101"), "I": ("111", "010", "010", "010", "111"),
    "L": ("100", "100", "100", "100", "111"), "M": ("101", "111", "111", "101", "101"),
    "O": ("111", "101", "101", "101", "111"), "R": ("110", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"), "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "101", "111", "010"), " ": ("000",) * 5,
}


def _art_planes(art: bytes, w: int, h: int):
    source = Playfield(w, h, MODE_INDEXED)
    mask = Playfield(w, h, MODE_INDEXED)
    source.pixels[:] = art
    mask.pixels[:] = bytes(1 if value else 0 for value in art)
    return source, mask


_ALIEN_PLANES = (_art_planes(ALIEN_A, 8, 8), _art_planes(ALIEN_B, 8, 8))


def _draw_text(pf, x: int, y: int, text: str, color: int = 6) -> None:
    for char in text:
        for gy, row in enumerate(FONT.get(char, FONT[" "])):
            for gx, bit in enumerate(row):
                if bit == "1":
                    pf.put(x + gx, y + gy, color)
        x += 4


def _shield_template():
    shield = Playfield(28, 16, MODE_INDEXED)
    blitter.fill(shield, 4, 0, 20, 2, 3)
    blitter.fill(shield, 2, 2, 24, 4, 3)
    blitter.fill(shield, 0, 6, 28, 8, 3)
    blitter.fill(shield, 0, 14, 8, 2, 3)
    blitter.fill(shield, 20, 14, 8, 2, 3)
    return shield


def _reset_shields(view) -> None:
    view.pf1.fill(0)
    template = _shield_template()
    for x in (42, 106, 170, 234):
        blitter.copy(template, view.pf1, 0, 0, x, 150, 28, 16)
    blitter.fill(view.pf1, 8, 188, 304, 1, 3)


def _damage_shield(view, x: int, y: int) -> bool:
    hit = any(view.pf1.get(px, py) for py in range(y - 2, y + 4)
              for px in range(x - 3, x + 4))
    if hit:
        blitter.fill(view.pf1, x - 3, y - 2, 7, 6, 0)
    return hit


def _configure_sound():
    for channel, freq, duration, pan in zip(
            paula.channel, (70, 920, 150, 210), (55, 55, 180, 500),
            (48, 208, 128, 128)):
        channel.sample = square_pcm(freq, duration, amp=7500)
        channel.rate, channel.volume, channel.pan = 8000, 24, pan
        channel.loop = False


async def main(*args, **kwargs) -> None:
    view = View(W, H, mode=MODE_INDEXED, scale=3)
    view.palette[:8] = [0x020608, 0x081018, 0xE8F0E8, 0x50F070,
                        0xF05050, 0xFFD060, 0xFFFFFF, 0x000000]
    view.bplcon, view.key_color = 1, 0
    view.copper.instructions = [
        Wait(0), Move("DIWSTART", 0), Move("DIWSTOP", H - 1),
        Move("BPLCON", 1), Move("KEY_COLOR", 0), Move("COLOR00", 0x020608),
        Wait(28), Move("COLOR02", 0xF05050),
        Wait(58), Move("COLOR02", 0xFFD060),
        Wait(88), Move("COLOR02", 0x50F070),
        Wait(145), Move("COLOR03", 0x40D060),
        Wait(175), Move("COLOR02", 0xE8F0E8),
    ]
    _reset_shields(view)
    _configure_sound()

    player, player_shot, enemy_shot, ufo, explosion, second_enemy_shot, commander = view.sprites[:7]
    player.place(PLAYER, 8, 8, x=156, y=PLAYER_Y, key_color=0)
    for sprite in view.sprites[1:7]:
        sprite.enabled = False
    # Sprite 7 is the control guide installed by run_view.

    aliens = [[True] * ALIEN_COLS for _ in range(ALIEN_ROWS)]
    formation_x = formation_y = 0
    direction, frame, score, high_score, lives, wave = 1, 0, 0, 0, 3, 1
    explosion_until = march_step = 0

    def alive_count():
        return sum(1 for row in aliens for alive in row if alive)

    def lowest_alive(col):
        for row in range(ALIEN_ROWS - 1, -1, -1):
            if aliens[row][col]:
                return row
        return None

    def reset_wave():
        nonlocal formation_x, formation_y, direction
        for row in range(ALIEN_ROWS):
            for col in range(ALIEN_COLS):
                aliens[row][col] = True
        formation_x = formation_y = 0
        direction = 1
        _reset_shields(view)

    def fire():
        if lives > 0 and not player_shot.enabled:
            player_shot.place(SHOT, 1, 6, x=player.x + 4, y=player.y - 7, key_color=0)
            paula.channel[1].stop(); paula.channel[1].play()

    def spawn_enemy_shot(sprite, col):
        row = lowest_alive(col)
        if row is not None and not sprite.enabled:
            x, y = invader_formation_position(col, row, formation_x, formation_y)
            sprite.place(SHOT, 1, 6, x=x + 4, y=y + 8, key_color=0)

    def hit_player():
        nonlocal lives, explosion_until
        if explosion_until > frame:
            return
        lives -= 1
        explosion.place(BURST, 8, 8, x=player.x, y=player.y, key_color=0)
        explosion_until = frame + 28
        player.enabled = False
        paula.channel[2].stop(); paula.channel[2].play()

    def redraw_playfields(anim):
        view.pf0.fill(0)
        blitter.fill(view.pf0, 0, 20, W, 1, 1)
        _draw_text(view.pf0, 8, 7,
                   "SCORE " + str(score).rjust(5, "0")[-5:] + "  HIGH " +
                   str(high_score).rjust(5, "0")[-5:] + "  WAVE " + str(wave))
        source, mask = _ALIEN_PLANES[anim]
        for row in range(ALIEN_ROWS):
            for col in range(ALIEN_COLS):
                if aliens[row][col]:
                    x, y = invader_formation_position(col, row, formation_x, formation_y)
                    blitter.cookie(source, mask, view.pf0, 0, 0, x, y, 8, 8)
        _draw_text(view.pf0, 8, 181, "LIVES " + str(max(0, lives)), 2)

    def tick(keys):
        nonlocal frame, formation_x, formation_y, direction, score
        nonlocal high_score, lives, wave, march_step, explosion_until
        frame += 1
        if explosion_until and frame >= explosion_until:
            explosion.enabled = False
            if lives > 0:
                player.enabled = True
            else:
                lives, score = 3, 0
                reset_wave()
                player.enabled = True
        if _gui_input.KEY_LEFT in keys: player.x = max(8, player.x - 3)
        if _gui_input.KEY_RIGHT in keys: player.x = min(W - 16, player.x + 3)

        remaining = alive_count()
        interval = max(2, 15 - (ALIEN_COLS * ALIEN_ROWS - remaining) // 5)
        if remaining and frame % interval == 0:
            formation_x += direction * 2
            xs = [invader_formation_position(c, r, formation_x, formation_y)[0]
                  for r in range(ALIEN_ROWS) for c in range(ALIEN_COLS) if aliens[r][c]]
            if min(xs) <= 8 or max(xs) + 8 >= W - 8:
                direction = -direction
                formation_x += direction * 2
                formation_y += 5
            paula.channel[0].sample = square_pcm((70, 82, 96, 110)[march_step], 45, amp=6500)
            paula.channel[0].stop(); paula.channel[0].play()
            march_step = (march_step + 1) % 4

        if player_shot.enabled:
            player_shot.y -= 6
            if player_shot.y < 20:
                player_shot.enabled = False
            else:
                for row in range(ALIEN_ROWS - 1, -1, -1):
                    for col in range(ALIEN_COLS):
                        if not aliens[row][col]: continue
                        x, y = invader_formation_position(col, row, formation_x, formation_y)
                        if aabb(player_shot.x, player_shot.y, 1, 6, x, y, 8, 8):
                            aliens[row][col] = player_shot.enabled = False
                            score += (ALIEN_ROWS - row) * 10
                            high_score = max(high_score, score)
                            explosion.place(BURST, 8, 8, x=x, y=y, key_color=0)
                            explosion_until = frame + 5
                            paula.channel[2].stop(); paula.channel[2].play()
                            break
                    else:
                        continue
                    break

        for shot in (enemy_shot, second_enemy_shot):
            if not shot.enabled: continue
            shot.y += 4
            if _damage_shield(view, shot.x, shot.y + 5) or shot.y > PLAYER_Y + 8:
                shot.enabled = False
            elif player.enabled and aabb(shot.x, shot.y, 1, 6, player.x, player.y, 8, 8):
                shot.enabled = False; hit_player()
        if frame % max(18, 65 - wave * 3) == 0:
            spawn_enemy_shot(enemy_shot, (frame // 7) % ALIEN_COLS)
        if wave > 1 and frame % 93 == 0:
            spawn_enemy_shot(second_enemy_shot, (frame // 11) % ALIEN_COLS)

        if not ufo.enabled and frame % 600 == 0:
            ufo.place(UFO, 12, 8, x=-12, y=23, key_color=0)
            paula.channel[3].loop = True
            paula.channel[3].loop_end = len(paula.channel[3].sample) // 2
            paula.channel[3].play()
        if ufo.enabled:
            ufo.x += 2
            if player_shot.enabled and aabb(player_shot.x, player_shot.y, 1, 6, ufo.x, ufo.y, 12, 8):
                player_shot.enabled = ufo.enabled = False
                score += 150; high_score = max(high_score, score)
                paula.channel[3].stop(); paula.channel[2].play()
            elif ufo.x > W:
                ufo.enabled = False; paula.channel[3].stop()

        commander.enabled = False
        for col in range(ALIEN_COLS):
            row = lowest_alive(col)
            if row is not None:
                x, y = invader_formation_position(col, row, formation_x, formation_y)
                commander.place(bytes((0, 5, 0, 5, 0, 5, 0, 5)), 8, 1, x=x, y=y - 2, key_color=0)
                break
        if alive_count() == 0:
            wave += 1; reset_wave()
        elif formation_y + 34 + (ALIEN_ROWS - 1) * 15 >= 145:
            hit_player(); reset_wave()
        redraw_playfields((frame // 12) & 1)

    def demo(frame_no):
        target = next((invader_formation_position(col, 0, formation_x, formation_y)[0]
                       for col in range(ALIEN_COLS) if lowest_alive(col) is not None), player.x)
        keys = {_gui_input.KEY_LEFT} if target < player.x - 2 else \
               ({_gui_input.KEY_RIGHT} if target > player.x + 2 else set())
        if frame_no % 13 == 0: keys.add(_gui_input.KEY_SPACE)
        return keys

    def on_exit():
        for channel in paula.channel: channel.stop()

    redraw_playfields(0)
    await run_view(view, tick, on_space=fire, on_exit=on_exit, demo=demo,
                   controls="LEFT/RIGHT MOVE  SPACE FIRE")


from apps._icons import invaders_icon

registry.register(name="invaders",
                  description="Alien Invaders — full-chipset fixed-screen arcade",
                  entry=main, icon_factory=invaders_icon, category="game")

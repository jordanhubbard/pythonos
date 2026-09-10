"""Path: /apps/demos/defender.py

An SDL-backed reconstruction of Williams' 1981 Defender, organized as a
teaching program. Read it in this order: indexed artwork and draw helpers,
the continuous PCM music/effects generator, circular-world state, enemy and
rescue rules, then the input/update/render loop.

The reconstruction keeps the original game's defining systems: a wrapping
world and scanner, separate thrust and reverse controls with momentum, ten
humans, Lander abduction, falling catches and safe returns, Mutants, Bombers,
mines, Pods, Swarmers, time-pressure Baiters, lasers, limited smart bombs,
risky hyperspace, waves, lives, scoring, sound effects, and background music.
It intentionally uses the normal desktop/SDL surface API rather than the
optional virtual-chipset laboratory.
"""

from __future__ import annotations

import asyncio
import struct

from apps import registry
from apps.arcade_logic import art_from_rows, circular_delta, flip_art_x
from kernel.gui import input as gui_input
from kernel.gui.compositor import CompositorWindow, compositor
from kernel.gui.menubar import Menu, MenuItem
from kernel.gui.sdl2.surface import SDL_FillRect, SDL_FillRects, SDL_Rect
from kernel.gui.text import text_renderer
from kernel.sound.mixer import mixer


# The old version used an 8x8 ship in a 320x200 emulated display. This window
# uses the native desktop path and enough resolution for terrain and radar to
# convey useful information without magnifying every logical pixel.
W, H = 940, 600
WORLD_W = 7000
HUD_H = 74
GROUND_BASE = 548
SHIP_SCREEN_X = W // 2
FPS = 50

COLORS = {
    0: 0x030711, 1: 0x28492D, 2: 0xE8F7FF, 3: 0xF05272,
    4: 0x54A8F5, 5: 0xFFD05A, 6: 0xB878FF, 7: 0x55E89B,
}


# Zero is transparent. The remaining digits map through COLORS. Keeping the
# pictures as strings makes changing a sprite approachable in the live editor.
SHIP_RIGHT = art_from_rows((
    "0000000020000000", "0000000222200000", "2200002225522220",
    "2222222222222222", "2200002222222220", "0000000222200000",
    "0000000020000000",
))
SHIP_LEFT = flip_art_x(SHIP_RIGHT, 16, 7)
LANDER = art_from_rows((
    "000033330000", "000333333000", "003335533300", "033333333330",
    "330333330330", "300033330030", "000300003000", "003000000300",
))
MUTANT = art_from_rows((
    "600000000006", "060660066060", "006666666600", "066606606660",
    "666666666666", "060666666060", "600060060006", "060000000060",
))
BOMBER = art_from_rows((
    "000444440000", "044444444440", "440044440044", "444444444444",
    "440044440044", "044444444440", "000440044000", "004400004400",
))
POD = art_from_rows((
    "000007700000", "000077770000", "007777777700", "077707707770",
    "777777777777", "077707707770", "007777777700", "000077770000",
))
SWARMER = art_from_rows((
    "006600", "066660", "666666", "066660", "006600", "060060",
))
BAITER = art_from_rows((
    "000666666000", "066600006660", "666666666666", "006666666600",
    "000660066000", "006600006600",
))
HUMAN = art_from_rows((
    "00500", "05550", "00500", "05550", "50505", "00500", "05050", "50005",
))


def _compile_art(pixels: bytes, width: int, height: int, scale: int = 2):
    """Turn adjacent indexed pixels into colored rectangles for SDL batching."""
    groups = {}
    for y in range(height):
        x = 0
        while x < width:
            color = pixels[y * width + x]
            if color == 0:
                x += 1
                continue
            end = x + 1
            while end < width and pixels[y * width + end] == color:
                end += 1
            groups.setdefault(color, []).append(
                (x * scale, y * scale, (end - x) * scale, scale))
            x = end
    return groups, width * scale, height * scale


ART = {
    "ship_right": _compile_art(SHIP_RIGHT, 16, 7),
    "ship_left": _compile_art(SHIP_LEFT, 16, 7),
    "lander": _compile_art(LANDER, 12, 8),
    "mutant": _compile_art(MUTANT, 12, 8),
    "bomber": _compile_art(BOMBER, 12, 8),
    "pod": _compile_art(POD, 12, 8),
    "swarmer": _compile_art(SWARMER, 6, 6),
    "baiter": _compile_art(BAITER, 12, 6),
    "human": _compile_art(HUMAN, 5, 8),
}


def _draw_art(surface, name: str, x: int, y: int) -> tuple[int, int]:
    """Draw one compiled sprite using one SDL_FillRects call per color."""
    groups, width, height = ART[name]
    for color, rectangles in groups.items():
        SDL_FillRects(surface,
                      [SDL_Rect(x + rx, y + ry, rw, rh)
                       for rx, ry, rw, rh in rectangles],
                      COLORS[color])
    return width, height


def _terrain_height(world_x: float) -> int:
    """Layer several integer waves into a repeatable mountainous skyline."""
    x = int(world_x) % WORLD_W
    broad = abs((x % 640) - 320) // 8
    medium = abs(((x + 173) % 230) - 115) // 5
    ridge = ((x * 17) % 43) // 7
    return 24 + broad + medium + ridge


def _ground_y(world_x: float) -> int:
    return GROUND_BASE - _terrain_height(world_x)


def _screen_x(world_x: float, camera_x: float) -> int:
    return SHIP_SCREEN_X + int(circular_delta(world_x, camera_x, WORLD_W))


def _visible(world_x: float, camera_x: float, margin: int = 30) -> bool:
    return abs(circular_delta(world_x, camera_x, WORLD_W)) <= W // 2 + margin


class GameAudio:
    """Continuous 48 kHz stereo PCM for music, engine, and queued effects.

    This is intentionally independent of Paula: normal SDL-style games feed
    the architecture-neutral system mixer. Buffered 250 ms periods
    HDA and virtio-sound supplied even during a slow TCP display transaction.
    """

    FRAMES = 12000
    # Frequencies are multiples of 4 Hz, so cached 250 ms square-wave blocks
    # join at the same phase instead of clicking at every dispatch boundary.
    MELODY = (200, 248, 300, 248, 200, 248, 348, 248,
              148, 200, 300, 200, 148, 200, 248, 200)
    CUES = {"laser": (1248, 1), "boom": (100, 1), "rescue": (900, 1),
            "abduct": (100, 1), "hyper": (500, 1), "bomb": (48, 2)}

    def __init__(self) -> None:
        self.tick = 0
        self.cue = None
        self.cue_left = 0
        self._cache = {}

    def trigger(self, name: str) -> None:
        if name in self.CUES:
            self.cue = name
            self.cue_left = self.CUES[name][1]

    def _block(self, note: int, thrusting: bool, cue: str | None) -> bytes:
        key = (note, thrusting, cue)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        effect = self.CUES[cue][0] if cue else 0
        data = bytearray(self.FRAMES * 4)
        for i in range(self.FRAMES):
            engine = 2100 if ((i * (148 if thrusting else 100)) // 24000) & 1 else -2100
            music = 1250 if ((i * note) // 24000) & 1 else -1250
            fx = 0
            if effect:
                fx = 5200 if ((i * effect) // 24000) & 1 else -5200
                fx = fx * (self.FRAMES - i) // self.FRAMES
            left = max(-32768, min(32767, engine + music + fx))
            right = max(-32768, min(32767, engine + music - fx // 3))
            struct.pack_into("<hh", data, i * 4, left, right)
        block = bytes(data)
        self._cache[key] = block
        return block

    def next_block(self, thrusting: bool) -> bytes:
        """Mix the next cached quarter-second musical period."""
        note = self.MELODY[self.tick % len(self.MELODY)]
        cue = self.cue if self.cue_left > 0 else None
        block = self._block(note, thrusting, cue)
        if self.cue_left > 0:
            self.cue_left -= 1
        self.tick += 1
        return block


async def main(*args, **kwargs) -> None:
    # A normal desktop game owns desktop input and the streaming mixer. If a
    # previous LoadView game was active, return it cleanly instead of leaving
    # its event handler and Paula clock underneath this window.
    from kernel.chipset import chipset
    chipset.release_to_workbench()

    win = CompositorWindow("Defender", x=42, y=30, w=W, h=H)
    compositor.add_window(win)
    surface = win.surface
    audio = GameAudio()
    audio_stream = None

    # Mutable world state. Entities retain logical coordinates even when they
    # are far beyond the viewport; the radar is the player's global view.
    keys = set()
    previous_keys = set()
    shots, mines, enemies, humans = [], [], [], []
    ship_world, ship_y, ship_speed, direction = 240.0, 310.0, 0.0, 1
    score, high_score, lives, bombs, wave = 0, 0, 3, 3, 1
    frame, wave_started, invulnerable_until = 0, 0, 0
    planet_alive, demo_mode, closed = True, False, False
    rng = 0x0D3F3AD

    def random(limit: int) -> int:
        nonlocal rng
        rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
        return rng % max(1, limit)

    def make_enemy(kind: str, x: float, y: float, dx: float = 0.0):
        enemy = {"kind": kind, "x": x % WORLD_W, "y": y, "dx": dx,
                 "captive": None, "alive": True}
        enemies.append(enemy)
        return enemy

    def reset_humans() -> None:
        humans.clear()
        for index in range(10):
            x = float(160 + index * (WORLD_W // 10))
            humans.append({"x": x, "y": float(_ground_y(x) - 16),
                           "state": "ground", "vy": 0.0, "carrier": None})

    def spawn_wave() -> None:
        nonlocal wave_started
        enemies.clear()
        mines.clear()
        for index in range(5 + min(4, wave)):
            make_enemy("lander", ship_world + 430 + index * 613,
                       HUD_H + 25 + (index * 37) % 180)
        for index in range(1 + wave // 2):
            make_enemy("bomber", ship_world + 850 + index * 997,
                       150 + (index * 83) % 190, -1.8 if index & 1 else 1.8)
        for index in range(1 + wave // 3):
            make_enemy("pod", ship_world + 1300 + index * 1200,
                       130 + (index * 61) % 210, 1.0)
        wave_started = frame

    def release_human(enemy) -> None:
        human = enemy.get("captive")
        if human is not None and human["state"] == "captive":
            human["state"], human["carrier"], human["vy"] = "falling", None, 0.6
        enemy["captive"] = None

    def destroy(enemy) -> None:
        nonlocal score, high_score
        if not enemy["alive"]:
            return
        enemy["alive"] = False
        release_human(enemy)
        score += {"lander": 150, "mutant": 150, "bomber": 250,
                  "pod": 1000, "swarmer": 150, "baiter": 200}[enemy["kind"]]
        high_score = max(high_score, score)
        if enemy["kind"] == "pod":
            for offset in (-26, 0, 26, 52):
                make_enemy("swarmer", enemy["x"] + offset,
                           enemy["y"] + random(25) - 12)
        audio.trigger("boom")

    def lose_ship() -> None:
        nonlocal lives, invulnerable_until, ship_speed
        if frame < invulnerable_until:
            return
        lives -= 1
        ship_speed = 0.0
        invulnerable_until = frame + 100
        audio.trigger("boom")

    def fire() -> None:
        if lives > 0 and len(shots) < 6:
            shots.append({"x": ship_world + direction * 20, "y": ship_y + 7,
                          "direction": direction, "ttl": 46})
            audio.trigger("laser")

    def smart_bomb() -> None:
        nonlocal bombs
        if bombs <= 0:
            return
        bombs -= 1
        for enemy in list(enemies):
            if enemy["alive"] and _visible(enemy["x"], ship_world, 0):
                destroy(enemy)
        audio.trigger("bomb")

    def hyperspace() -> None:
        nonlocal ship_world, ship_speed
        ship_world, ship_speed = float(random(WORLD_W)), 0.0
        audio.trigger("hyper")
        if random(8) == 0:
            lose_ship()

    def update_humans() -> None:
        nonlocal score, high_score
        for human in humans:
            state = human["state"]
            if state == "dead":
                continue
            if state == "captive":
                carrier = human["carrier"]
                if carrier is not None and carrier["alive"]:
                    human["x"], human["y"] = carrier["x"], carrier["y"] + 17
                continue
            if state == "falling":
                human["y"] += human["vy"]
                human["vy"] += 0.12
                if (abs(circular_delta(human["x"], ship_world, WORLD_W)) < 25
                        and abs(human["y"] - ship_y) < 19):
                    human["state"], human["vy"] = "aboard", 0.0
                    score += 500
                    audio.trigger("rescue")
                    continue
                ground = _ground_y(human["x"]) - 16
                if human["y"] >= ground:
                    human["y"] = float(ground)
                    human["state"] = "ground" if human["vy"] <= 3.0 else "dead"
                continue
            if state == "aboard":
                human["x"], human["y"] = ship_world, ship_y + 14
                ground = _ground_y(ship_world) - 16
                if ship_y + 14 >= ground - 4:
                    human["state"], human["y"] = "ground", float(ground)
                    score += 500
                    high_score = max(high_score, score)
                    audio.trigger("rescue")
                continue

            # A slow low pass also permits a direct pickup. The falling catch
            # remains worth more, but this makes the interaction discoverable.
            if (abs(circular_delta(human["x"], ship_world, WORLD_W)) < 20
                    and abs(human["y"] - ship_y) < 17
                    and abs(ship_speed) < 3.5):
                human["state"] = "aboard"
                score += 250
                audio.trigger("rescue")

    def update_enemies() -> None:
        ground_humans = [human for human in humans if human["state"] == "ground"]
        for enemy in list(enemies):
            if not enemy["alive"]:
                continue
            kind = enemy["kind"]
            if kind == "lander":
                captive = enemy["captive"]
                if captive is not None:
                    enemy["y"] -= 0.7
                    if enemy["y"] <= HUD_H + 3:
                        captive["state"], captive["carrier"] = "dead", None
                        enemy["captive"], enemy["kind"] = None, "mutant"
                        audio.trigger("abduct")
                elif ground_humans:
                    target = min(ground_humans, key=lambda human: abs(
                        circular_delta(human["x"], enemy["x"], WORLD_W)))
                    delta = circular_delta(target["x"], enemy["x"], WORLD_W)
                    enemy["x"] = (enemy["x"] + max(-1.5, min(1.5, delta))) % WORLD_W
                    target_y = target["y"] - 16
                    enemy["y"] += max(-0.9, min(0.9, target_y - enemy["y"]))
                    if abs(delta) < 6 and abs(enemy["y"] - target_y) < 4:
                        target["state"], target["carrier"] = "captive", enemy
                        enemy["captive"] = target
                        audio.trigger("abduct")
            elif kind in ("mutant", "swarmer", "baiter"):
                speed = {"mutant": 3.1, "swarmer": 4.0, "baiter": 5.2}[kind]
                delta = circular_delta(ship_world, enemy["x"], WORLD_W)
                enemy["x"] = (enemy["x"] + max(-speed, min(speed, delta))) % WORLD_W
                enemy["y"] += max(-speed, min(speed, ship_y - enemy["y"])) * 0.6
            elif kind == "bomber":
                enemy["x"] = (enemy["x"] + enemy["dx"]) % WORLD_W
                if frame % 140 == 0 and len(mines) < 22:
                    mines.append({"x": enemy["x"], "y": enemy["y"], "ttl": 1100})
            elif kind == "pod":
                enemy["x"] = (enemy["x"] + enemy["dx"]) % WORLD_W

            if (frame >= invulnerable_until
                    and abs(circular_delta(enemy["x"], ship_world, WORLD_W)) < 27
                    and abs(enemy["y"] - ship_y) < 18):
                lose_ship()

    def update_weapons() -> None:
        for shot in list(shots):
            shot["x"] = (shot["x"] + shot["direction"] * 19) % WORLD_W
            shot["ttl"] -= 1
            if shot["ttl"] <= 0:
                shots.remove(shot)
                continue
            for enemy in enemies:
                if (enemy["alive"]
                        and abs(circular_delta(enemy["x"], shot["x"], WORLD_W)) < 22
                        and abs(enemy["y"] - shot["y"]) < 17):
                    destroy(enemy)
                    shots.remove(shot)
                    break
        enemies[:] = [enemy for enemy in enemies if enemy["alive"]]

        for mine in list(mines):
            mine["ttl"] -= 1
            if mine["ttl"] <= 0:
                mines.remove(mine)
            elif (frame >= invulnerable_until
                  and abs(circular_delta(mine["x"], ship_world, WORLD_W)) < 15
                  and abs(mine["y"] - ship_y) < 14):
                mines.remove(mine)
                lose_ship()

    def render() -> None:
        """Render a native SDL frame; batched rectangles keep TCP traffic small."""
        SDL_FillRect(surface, None, COLORS[0])
        SDL_FillRect(surface, SDL_Rect(0, 0, W, HUD_H), 0x07101E)

        stars = []
        for index in range(90):
            world_x = (index * 379 + 71) % WORLD_W
            sx = _screen_x(world_x, ship_world)
            if 0 <= sx < W:
                stars.append(SDL_Rect(sx, HUD_H + 15 + (index * 47) % 330, 2, 2))
        SDL_FillRects(surface, stars, 0x9AB2CF)

        terrain = []
        for sx in range(0, W, 3):
            world_x = (ship_world + sx - SHIP_SCREEN_X) % WORLD_W
            ground = _ground_y(world_x)
            terrain.append(SDL_Rect(sx, ground, 3, GROUND_BASE - ground + 15))
        SDL_FillRects(surface, terrain, COLORS[1])

        # Scanner: every world object is visible even when the main viewport
        # cannot see it. The bright bracket shows the current viewport span.
        radar_x, radar_y, radar_w = 238, 39, 464
        SDL_FillRect(surface, SDL_Rect(radar_x, radar_y, radar_w, 25), 0x010307)
        SDL_FillRect(surface, SDL_Rect(radar_x, radar_y, radar_w, 2), COLORS[7])
        SDL_FillRect(surface, SDL_Rect(radar_x, radar_y + 23, radar_w, 2), COLORS[7])
        viewport_w = max(4, W * radar_w // WORLD_W)
        viewport_x = radar_x + int((ship_world - W / 2) % WORLD_W * radar_w / WORLD_W)
        SDL_FillRect(surface, SDL_Rect(viewport_x, radar_y + 2,
                                      min(viewport_w, radar_x + radar_w - viewport_x),
                                      21), 0x183050)
        for human in humans:
            if human["state"] != "dead":
                x = radar_x + int(human["x"] * radar_w / WORLD_W)
                SDL_FillRect(surface, SDL_Rect(x, radar_y + 17, 3, 4), COLORS[5])
        for enemy in enemies:
            x = radar_x + int(enemy["x"] * radar_w / WORLD_W)
            SDL_FillRect(surface, SDL_Rect(x, radar_y + 5, 4, 4), COLORS[3])
        x = radar_x + int(ship_world * radar_w / WORLD_W)
        SDL_FillRect(surface, SDL_Rect(x, radar_y + 11, 5, 5), COLORS[2])

        for enemy in enemies:
            sx = _screen_x(enemy["x"], ship_world)
            if -35 < sx < W + 35:
                _draw_art(surface, enemy["kind"], sx - 12, int(enemy["y"]))
        for human in humans:
            if human["state"] != "dead":
                sx = _screen_x(human["x"], ship_world)
                if -15 < sx < W + 15:
                    _draw_art(surface, "human", sx - 5, int(human["y"]))
        for shot in shots:
            sx = _screen_x(shot["x"], ship_world)
            SDL_FillRect(surface, SDL_Rect(sx - (28 if shot["direction"] < 0 else 0),
                                           int(shot["y"]) + 7, 29, 2), COLORS[2])
        for mine in mines:
            sx = _screen_x(mine["x"], ship_world)
            SDL_FillRect(surface, SDL_Rect(sx - 3, int(mine["y"]), 6, 6), COLORS[4])

        if frame >= invulnerable_until or (frame // 3) & 1:
            _draw_art(surface, "ship_right" if direction > 0 else "ship_left",
                      SHIP_SCREEN_X - 16, int(ship_y))

        text_renderer.draw(surface, 14, 11, f"SCORE {score:06d}", COLORS[2], size=15)
        text_renderer.draw(surface, 397, 11, f"HIGH {high_score:06d}", COLORS[2], size=15)
        text_renderer.draw(surface, 764, 11, f"WAVE {wave}", COLORS[2], size=15)
        text_renderer.draw(surface, 14, 43, f"SHIPS {max(0, lives)}", COLORS[5], size=13)
        text_renderer.draw(surface, 770, 43, f"BOMBS {bombs}", COLORS[5], size=13)
        humans_left = sum(human["state"] != "dead" for human in humans)
        status = "PLANET DESTROYED" if not planet_alive else f"HUMANS {humans_left}/10"
        text_renderer.draw(surface, 14, 76, status, COLORS[7], size=13)
        guide = ("UP/DOWN fly   X thrust   R reverse   SPACE fire   B smart bomb   "
                 "H hyperspace   D demo   ESC exit")
        text_renderer.draw(surface, 20, H - 24, guide, 0xB7C5D8, size=12)
        if demo_mode:
            text_renderer.draw(surface, W - 126, 77, "DEMO MODE", COLORS[5], size=13)
        win.dirty = True

    def set_demo() -> None:
        nonlocal demo_mode
        demo_mode = not demo_mode
        keys.clear()

    def on_event(event) -> None:
        nonlocal closed
        if event.kind == gui_input.QUIT:
            closed = True
        elif event.kind == gui_input.EVENT_KEY_DOWN:
            if event.code == gui_input.KEY_ESC:
                closed = True
            elif event.code in (ord("d"), ord("D")) and event.code not in keys:
                set_demo()
            keys.add(event.code)
        elif event.kind == gui_input.EVENT_KEY_UP:
            keys.discard(event.code)

    win.set_event_handler(on_event)
    win.menus = [Menu("Game", [
        MenuItem("Toggle Demo (D)", action=set_demo),
        MenuItem.sep(),
        MenuItem("Close (Esc)", action=win.close),
    ])]

    reset_humans()
    spawn_wave()
    audio_stream = mixer.start_stream(
        lambda: audio.next_block(
            ord("x") in keys or ord("X") in keys or demo_mode),
        period_ms=250, prebuffer=1, threaded=False)

    try:
        while not closed and not win._closed:
            frame += 1
            active = set(keys)
            if demo_mode:
                active = {ord("x")}
                active.add(gui_input.KEY_UP if (frame // 120) & 1 else gui_input.KEY_DOWN)
                if frame % 7 == 0:
                    active.add(gui_input.KEY_SPACE)
                if frame % 270 == 0:
                    active.add(ord("r"))
                if frame % 900 == 0:
                    active.add(ord("b"))
            pressed = active - previous_keys

            if gui_input.KEY_UP in active:
                ship_y = max(HUD_H + 18, ship_y - 4)
            if gui_input.KEY_DOWN in active:
                ship_y = min(_ground_y(ship_world) - 16, ship_y + 4)
            thrusting = ord("x") in active or ord("X") in active
            ship_speed += direction * 0.30 if thrusting else 0.0
            if not thrusting:
                ship_speed *= 0.992
            ship_speed = max(-9.0, min(9.0, ship_speed))
            ship_world = (ship_world + ship_speed) % WORLD_W
            ship_y = min(ship_y, float(_ground_y(ship_world) - 16))

            if ord("r") in pressed or ord("R") in pressed:
                direction = -direction
            if gui_input.KEY_SPACE in active and frame % 5 == 0:
                fire()
            if ord("b") in pressed or ord("B") in pressed:
                smart_bomb()
            if ord("h") in pressed or ord("H") in pressed:
                hyperspace()

            update_humans()
            update_enemies()
            update_weapons()

            living = sum(human["state"] != "dead" for human in humans)
            if living == 0 and planet_alive:
                planet_alive = False
                for enemy in enemies:
                    if enemy["kind"] == "lander":
                        enemy["kind"] = "mutant"
                audio.trigger("bomb")
            if not enemies:
                score += living * 100
                high_score = max(high_score, score)
                wave += 1
                if wave % 3 == 0:
                    bombs += 1
                if not planet_alive:
                    reset_humans()
                    planet_alive = True
                spawn_wave()
            if frame - wave_started > FPS * 24 and not any(
                    enemy["kind"] == "baiter" for enemy in enemies):
                make_enemy("baiter", ship_world - direction * 430, ship_y - 40)
            if lives <= 0:
                lives, score, bombs = 3, 0, 3
                reset_humans()
                spawn_wave()

            # The main game task already receives regular scheduling. Pumping
            # here avoids normal-GIL starvation of a competing Python thread;
            # the bounded device queue applies backpressure when already full.
            audio_stream.pump()
            if frame % 2 == 0:
                render()
            previous_keys = active
            await asyncio.sleep(1.0 / FPS)
    finally:
        if audio_stream is not None:
            audio_stream.stop()
        win.close()
        compositor.remove_window(win)


from apps._icons import defender_icon

registry.register(
    name="defender",
    description="Defender — scrolling world, scanner, abduction and rescue",
    entry=main,
    icon_factory=defender_icon,
    category="game",
)

# -*- coding: utf-8 -*-
"""
Клиент сетевой артиллерийской игры (720p, pygame).

Запуск:
    python3 client.py <ip сервера> [порт] [имя]

Управление:
    - Кликните в текстовое поле внизу, введите функцию траектории y=f(x)
    - Enter или кнопка "Огонь!" — выстрел
    - "move dx dy" — перемещение, затем можно стрелять
    - Кнопка камеры — поворот вида по углу СК
"""
import base64
import json
import math
import os
import random as rnd
import re
import socket
import sys
import threading
import time

import pygame

import shared as S

BG_COLOR = (18, 22, 28)
OBSTACLE_COLOR = (110, 100, 90)
GRID_LINE_COLOR = (30, 36, 44)
TEXT_COLOR = (230, 230, 230)
HP_COLOR = (90, 220, 110)
HP_BG = (70, 30, 30)
AXIS_X_COLOR = (90, 170, 255)
AXIS_Y_COLOR = (255, 150, 90)
TRAJ_COLOR = (255, 230, 80)
EXPLOSION_COLOR = (255, 100, 40)
MOVE_RADIUS_COLOR = (80, 200, 180)
MOVE_DEST_COLOR = (80, 255, 180)

PLAYER_COLORS = [
    (220, 70, 70), (70, 160, 230), (90, 210, 100),
    (230, 200, 70), (190, 100, 220), (240, 150, 60),
]

_SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wormix_sounds")

def _snd(name):
    try:
        return pygame.mixer.Sound(os.path.join(_SOUNDS_DIR, name))
    except Exception:
        return None

def _snd_list(*names):
    return [s for s in (_snd(n) for n in names) if s]


def _play(snd_list):
    if snd_list:
        s = rnd.choice(snd_list) if isinstance(snd_list, list) else snd_list
        if s:
            try:
                s.set_volume(0.7)
                s.play()
            except Exception:
                pass


class NetClient:
    def __init__(self, host, port, name):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.wfile = self.sock.makefile("w", encoding="utf-8", newline="\n")
        self.rfile = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.my_id = None
        self.lock = threading.Lock()
        self.inbox = []
        self.send({"type": "join", "name": name})
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def send(self, msg):
        try:
            self.wfile.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.wfile.flush()
        except Exception:
            pass

    def _reader(self):
        try:
            for line in self.rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                with self.lock:
                    self.inbox.append(msg)
        except Exception:
            pass

    def poll(self):
        with self.lock:
            msgs, self.inbox = self.inbox, []
        return msgs


class TextInputBox:
    """Поле ввода с поддержкой курсора, стрелок, Home/End."""

    def __init__(self, rect, font):
        self.rect = pygame.Rect(rect)
        self.font = font
        self.text = ""
        self.cursor = 0
        self.active = False

    def handle_event(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(ev.pos):
                self.active = True
                self._set_cursor_from_click(ev.pos[0])
            else:
                self.active = False
        elif ev.type == pygame.KEYDOWN and self.active:
            if ev.key == pygame.K_BACKSPACE:
                if self.cursor > 0:
                    self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                    self.cursor -= 1
            elif ev.key == pygame.K_DELETE:
                if self.cursor < len(self.text):
                    self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
            elif ev.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif ev.key == pygame.K_RIGHT:
                self.cursor = min(len(self.text), self.cursor + 1)
            elif ev.key == pygame.K_HOME:
                self.cursor = 0
            elif ev.key == pygame.K_END:
                self.cursor = len(self.text)
            elif ev.key == pygame.K_RETURN:
                return "submit"
            elif ev.unicode and ev.unicode.isprintable():
                if len(self.text) < 200:
                    self.text = self.text[:self.cursor] + ev.unicode + self.text[self.cursor:]
                    self.cursor += 1
        return None

    def _set_cursor_from_click(self, mx):
        best = 0
        best_dist = abs(mx - (self.rect.x + 10))
        for i in range(len(self.text) + 1):
            w = self.font.size(self.text[:i])[0]
            dist = abs(mx - (self.rect.x + 10 + w))
            if dist < best_dist:
                best_dist = dist
                best = i
        self.cursor = best

    def draw(self, surf):
        color = (250, 250, 250) if self.active else (170, 170, 170)
        pygame.draw.rect(surf, (40, 44, 52), self.rect, border_radius=6)
        pygame.draw.rect(surf, color, self.rect, width=2, border_radius=6)
        txt = self.text if self.text else "y(x) = ... или move x y (напр. move 1 2)"
        col = TEXT_COLOR if self.text else (120, 120, 120)
        text_surf = self.font.render(txt, True, col)
        clip = pygame.Rect(self.rect.x + 10, self.rect.y + 2, self.rect.w - 20, self.rect.h - 4)
        surf.set_clip(clip)
        surf.blit(text_surf, (self.rect.x + 10, self.rect.y + 8))
        if self.active:
            cx = self.font.size(self.text[:self.cursor])[0]
            cur_x = self.rect.x + 10 + cx
            pygame.draw.line(surf, (255, 255, 255),
                             (cur_x, self.rect.y + 6),
                             (cur_x, self.rect.y + self.rect.h - 6), 1)
        surf.set_clip(None)


def _compute_preview(expr, px, py, angle, flip):
    try:
        fn = S.compile_function(expr)
    except Exception:
        return []
    points = [(int(px), int(py))]
    step_grid = S.TRAJ_STEP / S.GRID_UNIT_PX
    max_grid = S.TRAJ_MAXX / S.GRID_UNIT_PX
    steps = int(max_grid / step_grid)
    x = 0.0
    for _ in range(steps):
        x_eval = -x if flip else x
        try:
            y = fn(x_eval)
        except Exception:
            break
        if not math.isfinite(y):
            break
        px_local = x_eval * S.GRID_UNIT_PX
        py_local = y * S.GRID_UNIT_PX
        dx, dy = S.rotate_point(px_local, py_local, angle)
        wx = px + dx
        wy = py - dy
        points.append((int(wx), int(wy)))
        if wx < 0 or wx >= S.WIDTH:
            break
        x += step_grid
    return points


def _parse_move(text):
    parts = text.strip().split()
    if len(parts) < 2 or parts[0].lower() != "move":
        return None, None
    if len(parts) == 2:
        try:
            dx = float(parts[1])
            return (dx, 0.0)
        except ValueError:
            return None, None
    if len(parts) == 3:
        try:
            dx, dy = float(parts[1]), float(parts[2])
            return (dx, dy)
        except ValueError:
            return None, None
    return None, None


def _move_dest_screen(dx, dy, px, py, angle):
    local_px = dx * S.GRID_UNIT_PX
    local_py = dy * S.GRID_UNIT_PX
    wx, wy = S.rotate_point(local_px, local_py, angle)
    return (int(px + wx), int(py - wy))


def _circle_points(cx, cy, r, segments=80):
    pts = []
    for i in range(segments + 1):
        a = 2 * math.pi * i / segments
        pts.append((int(cx + r * math.cos(a)), int(cy + r * math.sin(a))))
    return pts


def _world_to_screen(wx, wy, cam_on, cam_px, cam_py, cam_angle, cam_pan_x=0.0, cam_pan_y=0.0):
    if not cam_on:
        return int(wx), int(wy)
    lx, ly = S.rotate_point(wx - cam_px, cam_py - wy, -cam_angle)
    return int(cam_px + cam_pan_x + lx), int(cam_py + cam_pan_y - ly)


def _screen_to_world(sx, sy, cam_on, cam_px, cam_py, cam_angle, cam_pan_x=0.0, cam_pan_y=0.0):
    if not cam_on:
        return float(sx), float(sy)
    lx = sx - cam_px - cam_pan_x
    ly = cam_py + cam_pan_y - sy
    wx, wy_rot = S.rotate_point(lx, ly, cam_angle)
    return wx + cam_px, cam_py - wy_rot


def _screen_to_local(sx, sy, cam_on, cam_px, cam_py, cam_angle, player_angle, cam_pan_x=0.0, cam_pan_y=0.0):
    if cam_on:
        lx = sx - cam_px - cam_pan_x
        ly = cam_py + cam_pan_y - sy
    else:
        dx_w = sx - cam_px
        dy_w = cam_py - sy
        lx, ly = S.rotate_point(dx_w, dy_w, -player_angle)
    return lx / S.GRID_UNIT_PX, ly / S.GRID_UNIT_PX


def _is_move_input(text):
    t = text.strip().lower()
    return t == "move" or t.startswith("move ") and _parse_move(text.strip())[0] is None


def main():
    if len(sys.argv) < 2:
        print("Использование: python3 client.py <ip сервера> [порт] [имя]")
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else S.PROTOCOL_PORT_DEFAULT
    name = sys.argv[3] if len(sys.argv) > 3 else f"Player{pygame.time.get_ticks() % 1000}"

    pygame.init()
    pygame.mixer.init()
    _bg_music_path = os.path.join(_SOUNDS_DIR, "Rovio_-_Bad_Piggies_Main_theme_65552836.mp3")
    if os.path.isfile(_bg_music_path):
        pygame.mixer.music.load(_bg_music_path)
        pygame.mixer.music.set_volume(0.15)
        pygame.mixer.music.play(-1)
    screen = pygame.display.set_mode((S.WIDTH, S.HEIGHT))
    pygame.display.set_caption("Артиллерия по сети — %s" % name)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)
    font_big = pygame.font.SysFont("consolas", 26, bold=True)
    font_small = pygame.font.SysFont("consolas", 14)

    W = "ru.pragmatix.wormix.data.worm"
    SND_TURN = _snd_list(
        f"3_{W}.speak_vpered_ru.mp3",
        f"5_{W}.speak_vboy_ru.mp3",
        f"6_{W}.speak_v_ataku_ru.mp3",
        f"17_{W}.speak_ogon_ru.mp3",
        f"23_{W}.speak_komandir_ru.mp3",
        f"28_{W}.speak_cel_obnarujena_ru.mp3",
    )
    SND_FIRE = _snd(f"17_{W}.speak_ogon_ru.mp3")
    SND_HIT = _snd_list(
        f"20_{W}.speak_nashol_ru.mp3",
        f"4_{W}.speak_vottebe_ru.mp3",
        f"12_{W}.speak_prijok2_ru.mp3",
    )
    SND_MISS = _snd_list(
        f"11_{W}.speak_promazal_ru.mp3",
        f"19_{W}.speak_nepopal_ru.mp3",
    )
    SND_MOVE = _snd_list(
        f"5_{W}.speak_vboy_ru.mp3",
        f"8_{W}.speak_smotri_ru.mp3",
        f"2_{W}.speak_yessir_ru.mp3",
    )
    SND_WIN = _snd(f"1_{W}.win_ru.mp3")
    SND_KILL = _snd_list(
        f"7_{W}.speak_ubit_ru.mp3",
        f"16_{W}.speak_pervayakrov_ru.mp3",
    )
    SND_DAMAGE = _snd_list(
        f"32_{W}.damage1_ru.mp3",
        f"31_{W}.damage2_ru.mp3",
        f"30_{W}.damage3_ru.mp3",
        f"29_{W}.damage4_ru.mp3",
    )
    SND_SHIELD = _snd_list(
        f"25_{W}.speak_hoho_ru.mp3",
        f"10_{W}.speak_smeh1_ru.mp3",
    )

    try:
        net = NetClient(host, port, name)
    except Exception as e:
        print("Не удалось подключиться к серверу:", e)
        sys.exit(1)

    input_box = TextInputBox((20, S.HEIGHT - 50, 900, 36), font)
    fire_btn = pygame.Rect(940, S.HEIGHT - 50, 140, 36)
    flip_btn = pygame.Rect(1090, S.HEIGHT - 50, 80, 36)
    camera_btn = pygame.Rect(1180, S.HEIGHT - 50, 80, 36)
    end_turn_btn = pygame.Rect(940, S.HEIGHT - 90, 140, 36)
    restart_btn = pygame.Rect(S.WIDTH // 2 - 100, S.HEIGHT // 2 + 20, 200, 40)

    flip_x = False
    camera_on = False
    cam_pan_x = 0.0
    cam_pan_y = 0.0

    state = {"started": False, "round": 1, "turn": None, "players": [], "log": ""}
    grid_surface = None
    grid_b64_cache = None
    error_msg = ""
    error_until = 0
    game_over_msg = None
    choosing_spawn = False
    restart_voted = False
    restart_voters = (0, 0)

    preview_points = []
    preview_last_text = ""
    mirror_points = []

    move_preview_dx = None
    move_preview_dy = None
    move_preview_has_coords = False
    moved_this_turn = False

    # анимация полёта снаряда
    anim_points = []
    anim_idx = 0
    anim_active = False
    anim_result = None
    last_anim_time = 0
    anim_hit_played = False
    pending_grid_b64 = None

    prev_turn_id = None
    prev_my_hp = None
    prev_my_x = None
    prev_my_y = None

    obstacle_cells = []

    def rebuild_grid_surface(grid_b64):
        nonlocal obstacle_cells
        raw = base64.b64decode(grid_b64)
        grid = S.bytes_to_grid(raw)
        surf = pygame.Surface((S.GRID_W, S.GRID_H))
        surf.fill((0, 0, 0))
        arr = pygame.PixelArray(surf)
        obstacle_cells = []
        _cell = S.GRID_CELL
        for gy in range(S.GRID_H):
            row = grid[gy]
            for gx in range(S.GRID_W):
                if row[gx]:
                    arr[gx, gy] = OBSTACLE_COLOR
                    obstacle_cells.append((gx * _cell + _cell / 2, gy * _cell + _cell / 2))
                else:
                    arr[gx, gy] = BG_COLOR
        del arr
        return pygame.transform.scale(surf, (S.WIDTH, S.HEIGHT))

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            if choosing_spawn and ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx, my = ev.pos
                if camera_on and _active:
                    wx, wy = _screen_to_world(mx, my, camera_on, cam_px, cam_py, cam_angle, cam_pan_x, cam_pan_y)
                    net.send({"type": "spawn", "x": wx, "y": wy})
                else:
                    net.send({"type": "spawn", "x": mx, "y": my})
                choosing_spawn = False
            elif game_over_msg and not restart_voted and ev.type == pygame.MOUSEBUTTONDOWN and restart_btn.collidepoint(ev.pos):
                net.send({"type": "restart_vote"})
                restart_voted = True
            else:
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    if camera_btn.collidepoint(ev.pos):
                        camera_on = not camera_on
                        if camera_on:
                            _turn_id = state.get("turn")
                            _act = next((p for p in state.get("players", []) if p["id"] == _turn_id and p["alive"]), None)
                            if _act:
                                cam_pan_x = S.WIDTH / 2 - _act["x"]
                                cam_pan_y = S.HEIGHT / 2 - _act["y"]
                            else:
                                cam_pan_x = 0.0
                                cam_pan_y = 0.0
                        else:
                            cam_pan_x = 0.0
                            cam_pan_y = 0.0
                    elif end_turn_btn.collidepoint(ev.pos) and can_fire and moved_this_turn:
                        net.send({"type": "end_turn"})
                        moved_this_turn = False
                        input_box.text = ""
                        input_box.cursor = 0
                        preview_points = []
                        mirror_points = []
                        preview_last_text = ""
                        move_preview_dx = None
                        move_preview_dy = None
                        move_preview_has_coords = False
                    elif _active and can_fire and _is_move_input(input_box.text):
                        lx, ly = _screen_to_local(ev.pos[0], ev.pos[1], camera_on,
                                                   _active["x"], _active["y"], _active["angle"],
                                                   _active["angle"], cam_pan_x, cam_pan_y)
                        d2 = lx * lx + ly * ly
                        if d2 < S.MOVE_MAX_R2:
                            input_box.text = f"move {lx:.2f} {ly:.2f}"
                            input_box.cursor = len(input_box.text)
                            input_box.active = True

                res = input_box.handle_event(ev)
                if input_box.text != preview_last_text:
                    preview_last_text = input_box.text
                    can_preview = (state.get("started") and turn_id == net.my_id
                                   and not game_over_msg and not choosing_spawn)
                    move_preview_dx = None
                    move_preview_dy = None
                    move_preview_has_coords = False
                    if can_preview and preview_last_text.strip():
                        txt_stripped = preview_last_text.strip()
                        if txt_stripped.lower().startswith("move"):
                            move_dx, move_dy = _parse_move(txt_stripped)
                            if move_dx is not None:
                                move_preview_dx = move_dx
                                move_preview_dy = move_dy
                                move_preview_has_coords = True
                            else:
                                move_preview_dx = 0.0
                                move_preview_dy = 0.0
                                move_preview_has_coords = False
                            preview_points = []
                            mirror_points = []
                        else:
                            me = next((p for p in players if p["id"] == net.my_id), None)
                            if me and me["alive"]:
                                preview_points = _compute_preview(
                                    txt_stripped, me["x"], me["y"],
                                    me["angle"], flip_x)
                                mirror_points = _compute_preview(
                                    txt_stripped, me["x"], me["y"],
                                    me["angle"], not flip_x)
                            else:
                                preview_points = []
                                mirror_points = []
                    else:
                        preview_points = []
                        mirror_points = []
                if ev.type == pygame.MOUSEBUTTONDOWN and fire_btn.collidepoint(ev.pos):
                    if input_box.text.strip():
                        txt = input_box.text.strip()
                        net.send({"type": "fire", "expr": txt, "flip": flip_x})
                        moved_this_turn = False
                        input_box.text = ""
                        input_box.cursor = 0
                        preview_points = []
                        mirror_points = []
                        preview_last_text = ""
                        move_preview_dx = None
                        move_preview_dy = None
                        move_preview_has_coords = False
                elif res == "submit":
                    if input_box.text.strip():
                        txt = input_box.text.strip()
                        parts = txt.split()
                        if len(parts) == 3 and parts[0].lower() == "move":
                            try:
                                dx, dy = float(parts[1]), float(parts[2])
                                net.send({"type": "move", "dx": dx, "dy": dy})
                                moved_this_turn = True
                            except ValueError:
                                net.send({"type": "fire", "expr": txt, "flip": flip_x})
                                moved_this_turn = False
                        else:
                            net.send({"type": "fire", "expr": txt, "flip": flip_x})
                            moved_this_turn = False
                        input_box.text = ""
                        input_box.cursor = 0
                        preview_points = []
                        mirror_points = []
                        preview_last_text = ""
                        move_preview_dx = None
                        move_preview_dy = None
                        move_preview_has_coords = False
                if ev.type == pygame.MOUSEBUTTONDOWN and flip_btn.collidepoint(ev.pos):
                    flip_x = not flip_x
                    if preview_last_text.strip() and state.get("started") and turn_id == net.my_id and not game_over_msg:
                        me = next((p for p in players if p["id"] == net.my_id), None)
                        if me and me["alive"]:
                            preview_points = _compute_preview(
                                preview_last_text.strip(), me["x"], me["y"],
                                me["angle"], flip_x)
                            mirror_points = _compute_preview(
                                preview_last_text.strip(), me["x"], me["y"],
                                me["angle"], not flip_x)
                if camera_on and _active:
                    if ev.type == pygame.MOUSEWHEEL:
                        _pan_step = 40
                        cam_pan_x -= ev.x * _pan_step
                        cam_pan_y += ev.y * _pan_step
                    if ev.type == pygame.KEYDOWN and not input_box.active:
                        _pan_step = 40
                        if ev.key == pygame.K_UP:
                            cam_pan_y += _pan_step
                        elif ev.key == pygame.K_DOWN:
                            cam_pan_y -= _pan_step
                        elif ev.key == pygame.K_LEFT:
                            cam_pan_x += _pan_step
                        elif ev.key == pygame.K_RIGHT:
                            cam_pan_x -= _pan_step

        for msg in net.poll():
            t = msg.get("type")
            if t == "welcome":
                net.my_id = msg["id"]
            elif t == "state":
                old_turn = state.get("turn")
                new_turn = msg.get("turn")
                if new_turn != old_turn:
                    if new_turn == net.my_id:
                        _play(SND_TURN)
                        moved_this_turn = False
                    camera_on = False
                    cam_pan_x = 0.0
                    cam_pan_y = 0.0
                if prev_my_hp is not None:
                    me = next((p for p in msg.get("players", []) if p["id"] == net.my_id), None)
                    if me and me["hp"] < prev_my_hp:
                        _play(SND_DAMAGE)
                state = msg
                if msg.get("started"):
                    choosing_spawn = False
                    restart_voted = False
                    restart_voters = (0, 0)
                if msg.get("grid_b64") != grid_b64_cache:
                    if anim_active:
                        pending_grid_b64 = msg.get("grid_b64")
                    else:
                        grid_b64_cache = msg.get("grid_b64")
                        grid_surface = rebuild_grid_surface(grid_b64_cache)
                me = next((p for p in msg.get("players", []) if p["id"] == net.my_id), None)
                if me:
                    prev_my_hp = me["hp"]
                    if not anim_active and prev_my_x is not None:
                        if abs(me["x"] - prev_my_x) > 0.1 or abs(me["y"] - prev_my_y) > 0.1:
                            _play(SND_MOVE)
                    prev_my_x = me["x"]
                    prev_my_y = me["y"]
                prev_turn_id = new_turn
            elif t == "shot":
                anim_points = [tuple(p) for p in msg["points"]]
                anim_idx = 0
                anim_active = True
                anim_result = msg["result"]
                last_anim_time = time.time()
                anim_hit_played = False
                _play(SND_FIRE)
            elif t == "error":
                error_msg = msg.get("msg", "")
                error_until = time.time() + 3.0
                if "переместились" in error_msg:
                    moved_this_turn = False
            elif t == "game_over":
                winner = msg.get("winner")
                game_over_msg = "Игра окончена!"
                choosing_spawn = False
                if winner is not None:
                    for p in state.get("players", []):
                        if p["id"] == winner:
                            game_over_msg = f"Победил: {p['name']}!"
                    if winner == net.my_id:
                        _play(SND_WIN)
                    else:
                        _play(SND_KILL)
                else:
                    game_over_msg = "Все игроки выбыли. Без победителя."
            elif t == "choose_spawn":
                choosing_spawn = True
                game_over_msg = None
                restart_voted = False
                if msg.get("grid_b64"):
                    grid_b64_cache = msg["grid_b64"]
                    grid_surface = rebuild_grid_surface(grid_b64_cache)
            elif t == "restart_status":
                restart_voters = (msg.get("voted", 0), msg.get("total", 0))

        # --- рисование ---
        players = state.get("players", [])
        turn_id = state.get("turn")
        _active = next((p for p in players if p["id"] == turn_id and p["alive"]), None)
        can_fire = state.get("started") and turn_id == net.my_id and not game_over_msg

        cam_px = _active["x"] if _active else 0
        cam_py = _active["y"] if _active else 0
        cam_angle = _active["angle"] if _active else 0

        def w2s(wx, wy):
            return _world_to_screen(wx, wy, camera_on, cam_px, cam_py, cam_angle, cam_pan_x, cam_pan_y)

        screen.fill(BG_COLOR)

        if grid_surface:
            if camera_on and _active:
                _cell = S.GRID_CELL
                _oc = OBSTACLE_COLOR
                _cells_too = []
                _vp = []
                for wx, wy in obstacle_cells:
                    _c0 = w2s(wx - _cell / 2, wy - _cell / 2)
                    _c1 = w2s(wx + _cell / 2, wy - _cell / 2)
                    _c2 = w2s(wx + _cell / 2, wy + _cell / 2)
                    _c3 = w2s(wx - _cell / 2, wy + _cell / 2)
                    _sx = min(_c0[0], _c1[0], _c2[0], _c3[0])
                    _sy = min(_c0[1], _c1[1], _c2[1], _c3[1])
                    _ex = max(_c0[0], _c1[0], _c2[0], _c3[0])
                    _ey = max(_c0[1], _c1[1], _c2[1], _c3[1])
                    if _ex >= 0 and _sx <= S.WIDTH and _ey >= 0 and _sy <= S.HEIGHT:
                        _cells_too.append((_c0, _c1, _c2, _c3))
                for _c0, _c1, _c2, _c3 in _cells_too:
                    pygame.draw.polygon(screen, _oc, [_c0, _c1, _c2, _c3])
            else:
                screen.blit(grid_surface, (0, 0))
        else:
            for gx in range(0, S.WIDTH, 80):
                pygame.draw.line(screen, GRID_LINE_COLOR, (gx, 0), (gx, S.HEIGHT))
            for gy in range(0, S.HEIGHT, 80):
                pygame.draw.line(screen, GRID_LINE_COLOR, (0, gy), (S.WIDTH, gy))

        _UNIT = S.GRID_UNIT_PX
        _ARROW = 14

        if _active:
            px, py = _active["x"], _active["y"]
            angle = _active["angle"]

            _half_diag = math.hypot(S.WIDTH, S.HEIGHT) / 2 + _UNIT * 2
            _AXIS_LEN = _half_diag
            _grid_extent = int(_half_diag / _UNIT) + 2

            _hnx = math.cos(angle)
            _hny = -math.sin(angle)
            if flip_x:
                _hnx, _hny = -_hnx, -_hny
            _scr_rect = [(0, 0), (S.WIDTH, 0), (S.WIDTH, S.HEIGHT), (0, S.HEIGHT)]
            if camera_on:
                _world_rect = [_screen_to_world(sx, sy, True, cam_px, cam_py, cam_angle, cam_pan_x, cam_pan_y) for sx, sy in _scr_rect]
            else:
                _world_rect = list(_scr_rect)
            _hp_pts = _world_rect[:]
            for _i in range(len(_world_rect)):
                _inp = _hp_pts
                _hp_pts = []
                if not _inp:
                    break
                _s = _inp[-1]
                for _e in _inp:
                    _si = (_s[0] - px) * _hnx + (_s[1] - py) * _hny >= 0
                    _ei = (_e[0] - px) * _hnx + (_e[1] - py) * _hny >= 0
                    if _ei:
                        if not _si:
                            _dx, _dy = _e[0] - _s[0], _e[1] - _s[1]
                            _den = _dx * _hnx + _dy * _hny
                            if abs(_den) > 1e-10:
                                _t = ((px - _s[0]) * _hnx + (py - _s[1]) * _hny) / _den
                                _hp_pts.append((_s[0] + _t * _dx, _s[1] + _t * _dy))
                        _hp_pts.append(_e)
                    elif _si:
                        _dx, _dy = _e[0] - _s[0], _e[1] - _s[1]
                        _den = _dx * _hnx + _dy * _hny
                        if abs(_den) > 1e-10:
                            _t = ((px - _s[0]) * _hnx + (py - _s[1]) * _hny) / _den
                            _hp_pts.append((_s[0] + _t * _dx, _s[1] + _t * _dy))
                    _s = _e
            if len(_hp_pts) >= 3:
                now = time.time()
                _pulse = (math.sin(now * 3) + 1) / 2
                _alpha = int(12 + 10 * _pulse)
                _hp_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
                _hp_screen = [w2s(wx, wy) for wx, wy in _hp_pts]
                pygame.draw.polygon(_hp_surf, (255, 255, 80, _alpha),
                    [(int(p[0]), int(p[1])) for p in _hp_screen])
                screen.blit(_hp_surf, (0, 0))

            _psx, _psy = w2s(px, py)
            grid_col = (50, 60, 75)
            for i in range(-_grid_extent, _grid_extent + 1):
                if i == 0:
                    continue
                _wx1, _wy1 = S.rotate_point(i * _UNIT, -_AXIS_LEN, angle)
                _wx2, _wy2 = S.rotate_point(i * _UNIT, _AXIS_LEN, angle)
                sx1, sy1 = w2s(px + _wx1, py - _wy1)
                sx2, sy2 = w2s(px + _wx2, py - _wy2)
                pygame.draw.line(screen, grid_col, (sx1, sy1), (sx2, sy2), 1)
                _wx3, _wy3 = S.rotate_point(-_AXIS_LEN, i * _UNIT, angle)
                _wx4, _wy4 = S.rotate_point(_AXIS_LEN, i * _UNIT, angle)
                sx3, sy3 = w2s(px + _wx3, py - _wy3)
                sx4, sy4 = w2s(px + _wx4, py - _wy4)
                pygame.draw.line(screen, grid_col, (sx3, sy3), (sx4, sy4), 1)
            _ap_wx, _ap_wy = S.rotate_point(_AXIS_LEN, 0, angle)
            _ap = w2s(px + _ap_wx, py - _ap_wy)
            pygame.draw.line(screen, AXIS_X_COLOR, (_psx, _psy), _ap, 2)
            _an_wx, _an_wy = S.rotate_point(-_AXIS_LEN, 0, angle)
            _neg_x = w2s(px + _an_wx, py - _an_wy)
            pygame.draw.line(screen, AXIS_X_COLOR, (_psx, _psy), _neg_x, 1)
            _am_wx, _am_wy = S.rotate_point(_AXIS_LEN - _ARROW, 0, angle)
            _am = w2s(px + _am_wx, py - _am_wy)
            _ad = (_ap[0] - _am[0], _ap[1] - _am[1])
            _ad_len = math.hypot(_ad[0], _ad[1]) or 1
            _perp = (-_ad[1] / _ad_len, _ad[0] / _ad_len)
            _a1 = (int(_ap[0] - _ad[0] + _perp[0] * _ARROW // 2), int(_ap[1] - _ad[1] + _perp[1] * _ARROW // 2))
            _a2 = (int(_ap[0] - _ad[0] - _perp[0] * _ARROW // 2), int(_ap[1] - _ad[1] - _perp[1] * _ARROW // 2))
            pygame.draw.polygon(screen, AXIS_X_COLOR, [_ap, _a1, _a2])
            _lbl = font_small.render("x", True, AXIS_X_COLOR)
            screen.blit(_lbl, (_ap[0] + 6, _ap[1] - 7))
            _bp_wx, _bp_wy = S.rotate_point(0, _AXIS_LEN, angle)
            _bp = w2s(px + _bp_wx, py - _bp_wy)
            pygame.draw.line(screen, AXIS_Y_COLOR, (_psx, _psy), _bp, 2)
            _bn_wx, _bn_wy = S.rotate_point(0, -_AXIS_LEN, angle)
            _neg_y = w2s(px + _bn_wx, py - _bn_wy)
            pygame.draw.line(screen, AXIS_Y_COLOR, (_psx, _psy), _neg_y, 1)
            _bm_wx, _bm_wy = S.rotate_point(0, _AXIS_LEN - _ARROW, angle)
            _bm = w2s(px + _bm_wx, py - _bm_wy)
            _bd = (_bp[0] - _bm[0], _bp[1] - _bm[1])
            _bd_len = math.hypot(_bd[0], _bd[1]) or 1
            _perp2 = (-_bd[1] / _bd_len, _bd[0] / _bd_len)
            _b1 = (int(_bp[0] - _bd[0] + _perp2[0] * _ARROW // 2), int(_bp[1] - _bd[1] + _perp2[1] * _ARROW // 2))
            _b2 = (int(_bp[0] - _bd[0] - _perp2[0] * _ARROW // 2), int(_bp[1] - _bd[1] - _perp2[1] * _ARROW // 2))
            pygame.draw.polygon(screen, AXIS_Y_COLOR, [_bp, _b1, _b2])
            _lbl2 = font_small.render("y", True, AXIS_Y_COLOR)
            screen.blit(_lbl2, (_bp[0] + 4, _bp[1] - 16))
            for i in range(-_grid_extent, _grid_extent + 1):
                if i == 0:
                    continue
                _twx, _twy = S.rotate_point(i * _UNIT, 0, angle)
                _tick_x = w2s(px + _twx, py - _twy)
                pygame.draw.line(screen, AXIS_X_COLOR,
                    (_tick_x[0], _tick_x[1] - 4), (_tick_x[0], _tick_x[1] + 4), 1)
                _active_dir_x = (not flip_x and i > 0) or (flip_x and i < 0)
                if _active_dir_x:
                    _tlbl = font_small.render(str(i), True, AXIS_X_COLOR)
                    screen.blit(_tlbl, (_tick_x[0] - 4, _tick_x[1] + 6))
                _twx2, _twy2 = S.rotate_point(0, i * _UNIT, angle)
                _tick_y = w2s(px + _twx2, py - _twy2)
                pygame.draw.line(screen, AXIS_Y_COLOR,
                    (_tick_y[0] - 4, _tick_y[1]), (_tick_y[0] + 4, _tick_y[1]), 1)
                if i > 0:
                    _tlbl2 = font_small.render(str(i), True, AXIS_Y_COLOR)
                    screen.blit(_tlbl2, (_tick_y[0] + 6, _tick_y[1] - 7))

        for idx, p in enumerate(players):
            color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
            if not p["alive"]:
                color = (70, 70, 70)
            sx, sy = w2s(p["x"], p["y"])
            pygame.draw.circle(screen, color, (sx, sy), S.PLAYER_RADIUS)
            border = (255, 255, 255) if p["id"] == turn_id and p["alive"] else (10, 10, 10)
            pygame.draw.circle(screen, border, (sx, sy), S.PLAYER_RADIUS, width=2)
            label = f"{p['name']}"
            you = "  (вы)" if p["id"] == net.my_id else ""
            screen.blit(font_small.render(label + you, True, TEXT_COLOR), (sx - 30, sy - S.PLAYER_RADIUS - 44))
            bw = 40
            pygame.draw.rect(screen, HP_BG, (sx - bw // 2, sy - S.PLAYER_RADIUS - 16, bw, 6))
            hp_w = int(bw * max(0, p["hp"]) / S.MAX_HP)
            pygame.draw.rect(screen, HP_COLOR, (sx - bw // 2, sy - S.PLAYER_RADIUS - 16, hp_w, 6))
            hp_txt = f"{p['hp']}/{S.MAX_HP}"
            hp_surf = font_small.render(hp_txt, True, HP_COLOR)
            screen.blit(hp_surf, (sx - hp_surf.get_width() // 2, sy - S.PLAYER_RADIUS - 28))
            if p.get("bonus_effect"):
                eff_txt = {"damage": "DMG", "shield": "SHD", "double_shot": "x2", "angle_reset": "ANG"}.get(p["bonus_effect"], "?")
                eff_surf = font_small.render(eff_txt, True, (200, 130, 255))
                screen.blit(eff_surf, (sx + S.PLAYER_RADIUS + 4, sy - S.PLAYER_RADIUS))

        bonus = state.get("bonus")
        if bonus:
            bx, by = w2s(bonus["x"], bonus["y"])
            hs = S.BONUS_SIZE // 2
            pulse = abs(math.sin(time.time() * 4)) * 80 + 175
            glow_color = (int(pulse), 50, int(pulse))
            pygame.draw.rect(screen, glow_color, (bx - hs - 3, by - hs - 3, S.BONUS_SIZE + 6, S.BONUS_SIZE + 6), width=2)
            pygame.draw.rect(screen, (180, 60, 255), (bx - hs, by - hs, S.BONUS_SIZE, S.BONUS_SIZE), width=2)
            btype = bonus.get("type", "")
            blabel = {"damage": "DMG", "shield": "SHD", "double_shot": "x2", "angle_reset": "ANG"}.get(btype, "?")
            bsurf = font_small.render(blabel, True, (200, 130, 255))
            screen.blit(bsurf, (bx - bsurf.get_width() // 2, by - 7))

        if _active and can_fire and _is_move_input(input_box.text):
            _mpx, _mpy = _active["x"], _active["y"]
            _max_r_px = math.sqrt(S.MOVE_MAX_R2) * S.GRID_UNIT_PX
            _pulse = (math.sin(time.time() * 3) + 1) / 2
            _alpha_r = int(60 + 40 * _pulse)
            _circ_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            if camera_on:
                _circle_pts = []
                for i in range(81):
                    a = 2 * math.pi * i / 80
                    cwx = _mpx + _max_r_px * math.cos(a)
                    cwy = _mpy + _max_r_px * math.sin(a)
                    _circle_pts.append(w2s(cwx, cwy))
            else:
                _circle_pts = _circle_points(_mpx, _mpy, _max_r_px)
            for _ci in range(len(_circle_pts) - 1):
                pygame.draw.line(_circ_surf, (MOVE_RADIUS_COLOR[0], MOVE_RADIUS_COLOR[1], MOVE_RADIUS_COLOR[2], _alpha_r),
                                 _circle_pts[_ci], _circle_pts[_ci + 1], 2)
            _fill_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            pygame.draw.polygon(_fill_surf, (MOVE_RADIUS_COLOR[0], MOVE_RADIUS_COLOR[1], MOVE_RADIUS_COLOR[2], 15), _circle_pts)
            screen.blit(_fill_surf, (0, 0))
            screen.blit(_circ_surf, (0, 0))

        if move_preview_dx is not None and not anim_active and _active:
            _mpx, _mpy = _active["x"], _active["y"]
            _mangle = _active["angle"]
            _max_r_px = math.sqrt(S.MOVE_MAX_R2) * S.GRID_UNIT_PX
            local_px = move_preview_dx * S.GRID_UNIT_PX
            local_py = move_preview_dy * S.GRID_UNIT_PX
            wx_d, wy_off = S.rotate_point(local_px, local_py, _mangle)
            _dest = w2s(_mpx + wx_d, _mpy - wy_off)
            _dest_pulse = (math.sin(time.time() * 5) + 1) / 2
            _dest_r = int(5 + 3 * _dest_pulse)
            pygame.draw.circle(screen, MOVE_DEST_COLOR, _dest, _dest_r)
            pygame.draw.circle(screen, (255, 255, 255), _dest, _dest_r, width=1)
            _valid = (move_preview_dx ** 2 + move_preview_dy ** 2) < S.MOVE_MAX_R2
            _dest_col = MOVE_DEST_COLOR if _valid else (255, 80, 80)
            _dest_label = f"({move_preview_dx:.1f}, {move_preview_dy:.1f})"
            _dest_surf = font_small.render(_dest_label, True, _dest_col)
            screen.blit(_dest_surf, (_dest[0] + 10, _dest[1] - 10))
            _line_col = (MOVE_DEST_COLOR[0], MOVE_DEST_COLOR[1], MOVE_DEST_COLOR[2], 100) if _valid else (255, 80, 80, 100)
            _line_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            _ps = w2s(_mpx, _mpy)
            pygame.draw.line(_line_surf, _line_col, _ps, _dest, 1)
            screen.blit(_line_surf, (0, 0))

        if preview_points and not anim_active and len(preview_points) >= 2:
            _pv_col = (100, 140, 200)
            _pv_dash, _pv_gap, _pv_w = 8, 5, 1
            for _i in range(len(preview_points) - 1):
                _sx, _sy = preview_points[_i]
                _ex, _ey = preview_points[_i + 1]
                if camera_on:
                    _asx, _asy = w2s(_sx, _sy)
                    _bsx, _bsy = w2s(_ex, _ey)
                    _dx, _dy = _bsx - _asx, _bsy - _asy
                else:
                    _asx, _asy = _sx, _sy
                    _bsx, _bsy = _ex, _ey
                    _dx, _dy = _ex - _sx, _ey - _sy
                _ln = math.hypot(_dx, _dy)
                if _ln < 1:
                    continue
                _ux, _uy = _dx / _ln, _dy / _ln
                _p = 0.0
                while _p < _ln:
                    _ax = _asx + _ux * _p
                    _ay = _asy + _uy * _p
                    _ep = min(_p + _pv_dash, _ln)
                    _bx = _asx + _ux * _ep
                    _by = _asy + _uy * _ep
                    pygame.draw.line(screen, _pv_col, (int(_ax), int(_ay)), (int(_bx), int(_by)), _pv_w)
                    _p += _pv_dash + _pv_gap

        if mirror_points and not anim_active and len(mirror_points) >= 2:
            _mr_col = (180, 100, 100)
            _mr_dash, _mr_gap, _mr_w = 6, 8, 1
            _mr_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            for _i in range(len(mirror_points) - 1):
                _sx, _sy = mirror_points[_i]
                _ex, _ey = mirror_points[_i + 1]
                if camera_on:
                    _asx, _asy = w2s(_sx, _sy)
                    _bsx, _bsy = w2s(_ex, _ey)
                    _dx, _dy = _bsx - _asx, _bsy - _asy
                else:
                    _asx, _asy = _sx, _sy
                    _bsx, _bsy = _ex, _ey
                    _dx, _dy = _ex - _sx, _ey - _sy
                _ln = math.hypot(_dx, _dy)
                if _ln < 1:
                    continue
                _ux, _uy = _dx / _ln, _dy / _ln
                _p = 0.0
                while _p < _ln:
                    _ax = _asx + _ux * _p
                    _ay = _asy + _uy * _p
                    _ep = min(_p + _mr_dash, _ln)
                    _bx = _asx + _ux * _ep
                    _by = _asy + _uy * _ep
                    pygame.draw.line(_mr_surf, (*_mr_col, 120), (int(_ax), int(_ay)), (int(_bx), int(_by)), _mr_w)
                    _p += _mr_dash + _mr_gap
            screen.blit(_mr_surf, (0, 0))

        now = time.time()
        if anim_active and anim_points:
            elapsed = now - last_anim_time
            target_idx = min(len(anim_points), int(elapsed * 220))
            anim_idx = max(anim_idx, target_idx)
            shown = anim_points[:max(2, anim_idx)]
            if len(shown) >= 2:
                if camera_on:
                    cam_pts = [w2s(p[0], p[1]) for p in shown]
                    pygame.draw.lines(screen, TRAJ_COLOR, False, cam_pts, 2)
                else:
                    pygame.draw.lines(screen, TRAJ_COLOR, False, shown, 2)
            if anim_idx >= len(anim_points):
                if not anim_hit_played:
                    anim_hit_played = True
                    if anim_result:
                        kind = anim_result.get("kind")
                        if kind == "player":
                            _play(SND_HIT)
                        elif kind == "shield":
                            _play(SND_SHIELD)
                        elif kind in ("obstacle", "miss"):
                            _play(SND_MISS)
                if anim_result and anim_result.get("pos"):
                    pos = anim_result["pos"]
                    kind = anim_result.get("kind")
                    psx, psy = w2s(pos[0], pos[1])
                    if kind in ("obstacle", "player"):
                        pygame.draw.circle(screen, EXPLOSION_COLOR, (int(psx), int(psy)), 18, width=3)
                    elif kind == "shield":
                        pygame.draw.circle(screen, (100, 180, 255), (int(psx), int(psy)), 18, width=3)
                if elapsed * 220 > len(anim_points) + 60:
                    anim_active = False
                    if pending_grid_b64 is not None:
                        grid_b64_cache = pending_grid_b64
                        grid_surface = rebuild_grid_surface(grid_b64_cache)
                        pending_grid_b64 = None
        elif anim_points and len(anim_points) >= 2:
            if camera_on:
                cam_pts = [w2s(p[0], p[1]) for p in anim_points]
                pygame.draw.lines(screen, TRAJ_COLOR, False, cam_pts, 2)
            else:
                pygame.draw.lines(screen, TRAJ_COLOR, False, anim_points, 2)

        # верхняя панель информации
        you_p = next((p for p in players if p["id"] == net.my_id), None)
        turn_p = next((p for p in players if p["id"] == turn_id), None)
        info = f"Раунд {state.get('round',1)}  |  "
        if state.get("started"):
            if turn_p:
                whose = "ВАШ ХОД!" if turn_id == net.my_id else f"ходит {turn_p['name']}"
                info += whose
            else:
                info += "ожидание..."
        else:
            info += "ожидание игроков для старта..."
        screen.blit(font_big.render(info, True, TEXT_COLOR), (20, 14))

        if you_p:
            you_info = f"Вы: {you_p['name']}   HP: {you_p['hp']}   угол вашей системы координат: {math.degrees(you_p['angle']):.1f}°"
            screen.blit(font.render(you_info, True, AXIS_X_COLOR), (20, 48))

        if turn_p and turn_id != net.my_id:
            angle_info = f"Угол СК {turn_p['name']}: {math.degrees(turn_p['angle']):.1f}°"
            angle_surf = font.render(angle_info, True, (200, 200, 200))
            screen.blit(angle_surf, (S.WIDTH - angle_surf.get_width() - 20, 14))

        turn_deadline = state.get("turn_deadline", 0)
        if state.get("started") and turn_deadline and not game_over_msg:
            remaining = max(0, turn_deadline - time.time())
            secs = int(remaining)
            timer_color = (255, 90, 90) if secs <= 10 else (230, 230, 230)
            timer_txt = f"{secs}s"
            timer_surf = font_big.render(timer_txt, True, timer_color)
            screen.blit(timer_surf, (S.WIDTH - timer_surf.get_width() - 20, 48))

        log_text = state.get("log", "")
        if log_text:
            screen.blit(font_small.render(log_text, True, (150, 200, 150)), (20, S.HEIGHT - 80))

        if error_msg and now < error_until:
            screen.blit(font.render(error_msg, True, (255, 90, 90)), (20, S.HEIGHT - 100))

        if game_over_msg:
            overlay = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            screen.blit(overlay, (0, 0))
            txt = font_big.render(game_over_msg, True, (255, 230, 100))
            screen.blit(txt, (S.WIDTH // 2 - txt.get_width() // 2, S.HEIGHT // 2 - 40))
            if not restart_voted:
                pygame.draw.rect(screen, (60, 140, 70), restart_btn, border_radius=6)
                screen.blit(font.render("Реванш!", True, TEXT_COLOR),
                    (restart_btn.x + 55, restart_btn.y + 10))
            else:
                voted, total = restart_voters
                wait_txt = f"Ожидание... ({voted}/{total})"
                screen.blit(font.render(wait_txt, True, (180, 180, 180)),
                    (S.WIDTH // 2 - 80, S.HEIGHT // 2 + 30))

        if choosing_spawn:
            overlay = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 100))
            screen.blit(overlay, (0, 0))
            txt = font_big.render("Нажмите на поле, чтобы выбрать стартовую позицию", True, (100, 220, 255))
            screen.blit(txt, (S.WIDTH // 2 - txt.get_width() // 2, S.HEIGHT // 2 - 20))

        input_box.draw(screen)
        btn_color = (60, 140, 70) if can_fire else (60, 60, 60)
        pygame.draw.rect(screen, btn_color, fire_btn, border_radius=6)
        screen.blit(font.render("Огонь!", True, TEXT_COLOR), (fire_btn.x + 30, fire_btn.y + 7))
        flip_color = (80, 120, 180) if can_fire else (60, 60, 60)
        pygame.draw.rect(screen, flip_color, flip_btn, border_radius=6)
        flip_label = "\u2190" if flip_x else "\u2192"
        screen.blit(font.render(flip_label, True, TEXT_COLOR), (flip_btn.x + 30, flip_btn.y + 7))
        cam_color = (180, 120, 60) if camera_on else (60, 60, 60)
        pygame.draw.rect(screen, cam_color, camera_btn, border_radius=6)
        screen.blit(font.render("CAM", True, TEXT_COLOR), (camera_btn.x + 15, camera_btn.y + 7))

        if can_fire and moved_this_turn:
            et_color = (120, 100, 180)
            pygame.draw.rect(screen, et_color, end_turn_btn, border_radius=6)
            screen.blit(font.render("Завершить ход", True, TEXT_COLOR),
                (end_turn_btn.x + 10, end_turn_btn.y + 7))

        if _active:
            mx, my = pygame.mouse.get_pos()
            if camera_on:
                lx_grid, ly_grid = _screen_to_local(mx, my, camera_on, cam_px, cam_py, cam_angle,
                                                     _active["angle"], cam_pan_x, cam_pan_y)
            else:
                dx_w = mx - _active["x"]
                dy_w = _active["y"] - my
                lx, ly = S.rotate_point(dx_w, dy_w, -_active["angle"])
                lx_grid = lx / S.GRID_UNIT_PX
                ly_grid = ly / S.GRID_UNIT_PX
            coord_txt = f"x: {lx_grid:.1f}  y: {ly_grid:.1f}"
            coord_surf = font_small.render(coord_txt, True, (180, 180, 180))
            screen.blit(coord_surf, (S.WIDTH - coord_surf.get_width() - 16, S.HEIGHT - 70))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()

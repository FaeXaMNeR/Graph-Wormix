# -*- coding: utf-8 -*-
"""
Клиент сетевой артиллерийской игры (720p, pygame).

Запуск:
    python3 client.py <ip сервера> [порт] [имя]

Управление:
    - Кликните в текстовое поле внизу, введите функцию траектории y=f(x)
      в СВОЕЙ повёрнутой системе координат (например: sin(x)/5, x*0.3, x**2/400 - 50 ...)
    - Enter или кнопка "Огонь!" — выстрел (доступно только в свой ход)
    - Разрешённые функции: sin cos tan asin acos atan sinh cosh tanh exp log log10
      sqrt abs floor ceil pow, константы pi, e. Переменная: x
"""
import base64
import json
import math
import os
import random as rnd
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
    def __init__(self, rect, font):
        self.rect = pygame.Rect(rect)
        self.font = font
        self.text = ""
        self.active = False

    def handle_event(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(ev.pos)
        elif ev.type == pygame.KEYDOWN and self.active:
            if ev.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif ev.key == pygame.K_RETURN:
                return "submit"
            elif ev.unicode and ev.unicode.isprintable():
                if len(self.text) < 200:
                    self.text += ev.unicode
        return None

    def draw(self, surf):
        color = (250, 250, 250) if self.active else (170, 170, 170)
        pygame.draw.rect(surf, (40, 44, 52), self.rect, border_radius=6)
        pygame.draw.rect(surf, color, self.rect, width=2, border_radius=6)
        txt = self.text if self.text else "y(x) = ... или move x y (напр. move 1 2)"
        col = TEXT_COLOR if self.text else (120, 120, 120)
        surf.blit(self.font.render(txt, True, col), (self.rect.x + 10, self.rect.y + 8))


def main():
    if len(sys.argv) < 2:
        print("Использование: python3 client.py <ip сервера> [порт] [имя]")
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else S.PROTOCOL_PORT_DEFAULT
    name = sys.argv[3] if len(sys.argv) > 3 else f"Player{pygame.time.get_ticks() % 1000}"

    pygame.init()
    pygame.mixer.init()
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
    flip_x = False
    restart_btn = pygame.Rect(S.WIDTH // 2 - 100, S.HEIGHT // 2 + 20, 200, 40)

    state = {"started": False, "round": 1, "turn": None, "players": [], "log": ""}
    grid_surface = None
    grid_b64_cache = None
    error_msg = ""
    error_until = 0
    game_over_msg = None
    choosing_spawn = False
    restart_voted = False
    restart_voters = (0, 0)

    # анимация полёта снаряда
    anim_points = []
    anim_idx = 0
    anim_active = False
    anim_result = None
    last_anim_time = 0

    prev_turn_id = None
    prev_my_hp = None
    prev_my_x = None
    prev_my_y = None

    def rebuild_grid_surface(grid_b64):
        raw = base64.b64decode(grid_b64)
        grid = S.bytes_to_grid(raw)
        surf = pygame.Surface((S.GRID_W, S.GRID_H))
        surf.fill((0, 0, 0))
        arr = pygame.PixelArray(surf)
        for gy in range(S.GRID_H):
            row = grid[gy]
            for gx in range(S.GRID_W):
                if row[gx]:
                    arr[gx, gy] = OBSTACLE_COLOR
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
                net.send({"type": "spawn", "x": mx, "y": my})
                choosing_spawn = False
            elif game_over_msg and not restart_voted and ev.type == pygame.MOUSEBUTTONDOWN and restart_btn.collidepoint(ev.pos):
                net.send({"type": "restart_vote"})
                restart_voted = True
            else:
                res = input_box.handle_event(ev)
                if res == "submit" or (ev.type == pygame.MOUSEBUTTONDOWN and fire_btn.collidepoint(ev.pos)):
                    if input_box.text.strip():
                        txt = input_box.text.strip()
                        parts = txt.split()
                        if len(parts) == 3 and parts[0].lower() == "move":
                            try:
                                dx, dy = float(parts[1]), float(parts[2])
                                net.send({"type": "move", "dx": dx, "dy": dy})
                            except ValueError:
                                net.send({"type": "fire", "expr": txt, "flip": flip_x})
                        else:
                            net.send({"type": "fire", "expr": txt, "flip": flip_x})
                        input_box.text = ""
                if ev.type == pygame.MOUSEBUTTONDOWN and flip_btn.collidepoint(ev.pos):
                    flip_x = not flip_x

        for msg in net.poll():
            t = msg.get("type")
            if t == "welcome":
                net.my_id = msg["id"]
            elif t == "state":
                old_turn = state.get("turn")
                new_turn = msg.get("turn")
                if new_turn == net.my_id and old_turn != net.my_id:
                    _play(SND_TURN)
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
                _play(SND_FIRE)
                if msg.get("shooter") == net.my_id:
                    kind = msg.get("result", {}).get("kind")
                    if kind == "player":
                        _play(SND_HIT)
                    elif kind == "shield":
                        _play(SND_SHIELD)
                    elif kind == "obstacle" or kind == "miss":
                        _play(SND_MISS)
            elif t == "error":
                error_msg = msg.get("msg", "")
                error_until = time.time() + 3.0
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
        screen.fill(BG_COLOR)
        if grid_surface:
            screen.blit(grid_surface, (0, 0))
        else:
            for gx in range(0, S.WIDTH, 80):
                pygame.draw.line(screen, GRID_LINE_COLOR, (gx, 0), (gx, S.HEIGHT))
            for gy in range(0, S.HEIGHT, 80):
                pygame.draw.line(screen, GRID_LINE_COLOR, (0, gy), (S.WIDTH, gy))

        players = state.get("players", [])
        turn_id = state.get("turn")

        # сетка и оси координат активного игрока (math: OX →, OY ↑)
        _UNIT = S.GRID_UNIT_PX
        _AXIS_LEN = 900
        _ARROW = 14
        _corners = [(0, 0), (S.WIDTH, 0), (S.WIDTH, S.HEIGHT), (0, S.HEIGHT)]

        _active = next((p for p in players if p["id"] == turn_id and p["alive"]), None)
        if _active:
            px, py = _active["x"], _active["y"]
            angle = _active["angle"]

            lx_vals, ly_vals = [], []
            for _cx, _cy in _corners:
                _lx, _ly = S.rotate_point(_cx - px, py - _cy, -angle)
                lx_vals.append(_lx)
                ly_vals.append(_ly)
            lx_min = int(min(lx_vals)) - _UNIT * 2
            lx_max = int(max(lx_vals)) + _UNIT * 2
            ly_min = int(min(ly_vals)) - _UNIT * 2
            ly_max = int(max(ly_vals)) + _UNIT * 2

            grid_col = (50, 60, 75)
            for gx in range((lx_min // _UNIT) * _UNIT, lx_max + 1, _UNIT):
                _wx1, _wy1 = S.rotate_point(gx, ly_min, angle)
                _wx2, _wy2 = S.rotate_point(gx, ly_max, angle)
                pygame.draw.line(screen, grid_col,
                    (int(px + _wx1), int(py - _wy1)),
                    (int(px + _wx2), int(py - _wy2)), 1)
            for gy in range((ly_min // _UNIT) * _UNIT, ly_max + 1, _UNIT):
                _wx1, _wy1 = S.rotate_point(lx_min, gy, angle)
                _wx2, _wy2 = S.rotate_point(lx_max, gy, angle)
                pygame.draw.line(screen, grid_col,
                    (int(px + _wx1), int(py - _wy1)),
                    (int(px + _wx2), int(py - _wy2)), 1)

            # --- подсветка активной полуплоскости ---
            _hnx = math.cos(angle)
            _hny = -math.sin(angle)
            if flip_x:
                _hnx, _hny = -_hnx, -_hny
            _screen_rect = [(0, 0), (S.WIDTH, 0), (S.WIDTH, S.HEIGHT), (0, S.HEIGHT)]
            _hp_pts = _screen_rect[:]
            for _i in range(len(_screen_rect)):
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
                _pulse = (math.sin(now * 3) + 1) / 2
                _alpha = int(12 + 10 * _pulse)
                _hp_surf = pygame.Surface((S.WIDTH, S.HEIGHT), pygame.SRCALPHA)
                pygame.draw.polygon(_hp_surf, (255, 255, 80, _alpha),
                    [(int(p[0]), int(p[1])) for p in _hp_pts])
                screen.blit(_hp_surf, (0, 0))

            # --- ось X: сплошная активная полуось, пунктирная неактивная ---
            _neg_end = S.rotate_point(-_AXIS_LEN, 0, angle)
            _pos_end = S.rotate_point(_AXIS_LEN, 0, angle)

            def _dashed_line(surf, col, from_local, to_local, dash=10, gap=6, w=1):
                sx, sy = int(px + from_local[0]), int(py - from_local[1])
                ex, ey = int(px + to_local[0]), int(py - to_local[1])
                dx, dy = ex - sx, ey - sy
                ln = math.hypot(dx, dy)
                if ln < 1:
                    return
                ux, uy = dx / ln, dy / ln
                p = 0.0
                while p < ln:
                    ax = sx + ux * p
                    ay = sy + uy * p
                    ep = min(p + dash, ln)
                    bx = sx + ux * ep
                    by = sy + uy * ep
                    pygame.draw.line(surf, col, (int(ax), int(ay)), (int(bx), int(by)), w)
                    p += dash + gap

            if flip_x:
                # активная: отрицательная полуось (←) — сплошная
                pygame.draw.line(screen, AXIS_X_COLOR,
                    (int(px), int(py)), (int(px + _neg_end[0]), int(py - _neg_end[1])), 2)
                _a1 = S.rotate_point(-_AXIS_LEN + _ARROW, _ARROW // 2, angle)
                _a2 = S.rotate_point(-_AXIS_LEN + _ARROW, -_ARROW // 2, angle)
                pygame.draw.polygon(screen, AXIS_X_COLOR, [
                    (int(px + _neg_end[0]), int(py - _neg_end[1])),
                    (int(px + _a1[0]), int(py - _a1[1])),
                    (int(px + _a2[0]), int(py - _a2[1])),
                ])
                _lbl = S.rotate_point(-_AXIS_LEN - 16, 0, angle)
                screen.blit(font_small.render("x", True, AXIS_X_COLOR),
                    (int(px + _lbl[0]) - 4, int(py - _lbl[1]) - 7))
                # неактивная: положительная полуось — пунктир
                _dashed_line(screen, AXIS_X_COLOR, (0, 0), _pos_end)
            else:
                # активная: положительная полуось (→) — сплошная
                pygame.draw.line(screen, AXIS_X_COLOR,
                    (int(px), int(py)), (int(px + _pos_end[0]), int(py - _pos_end[1])), 2)
                _a1 = S.rotate_point(_AXIS_LEN - _ARROW, _ARROW // 2, angle)
                _a2 = S.rotate_point(_AXIS_LEN - _ARROW, -_ARROW // 2, angle)
                pygame.draw.polygon(screen, AXIS_X_COLOR, [
                    (int(px + _pos_end[0]), int(py - _pos_end[1])),
                    (int(px + _a1[0]), int(py - _a1[1])),
                    (int(px + _a2[0]), int(py - _a2[1])),
                ])
                _lbl = S.rotate_point(_AXIS_LEN + 16, 0, angle)
                screen.blit(font_small.render("x", True, AXIS_X_COLOR),
                    (int(px + _lbl[0]) - 4, int(py - _lbl[1]) - 7))
                # неактивная: отрицательная полуось — пунктир
                _dashed_line(screen, AXIS_X_COLOR, (0, 0), _neg_end)

            # --- ось Y (без изменений) ---
            _dx2, _dy2 = S.rotate_point(0, _AXIS_LEN, angle)
            pygame.draw.line(screen, AXIS_Y_COLOR,
                (int(px), int(py)), (int(px + _dx2), int(py - _dy2)), 2)
            _b1x, _b1y = S.rotate_point(_ARROW // 2, _AXIS_LEN - _ARROW, angle)
            _b2x, _b2y = S.rotate_point(-_ARROW // 2, _AXIS_LEN - _ARROW, angle)
            pygame.draw.polygon(screen, AXIS_Y_COLOR, [
                (int(px + _dx2), int(py - _dy2)),
                (int(px + _b1x), int(py - _b1y)),
                (int(px + _b2x), int(py - _b2y)),
            ])
            _lbl2 = S.rotate_point(0, _AXIS_LEN + 16, angle)
            screen.blit(font_small.render("y", True, AXIS_Y_COLOR),
                (int(px + _lbl2[0]) - 4, int(py - _lbl2[1]) - 7))

            _num_units = _AXIS_LEN // _UNIT
            # засечки и числа на оси X
            for i in range(-_num_units, _num_units + 1):
                if i == 0:
                    continue
                _t1x, _t1y = S.rotate_point(i * _UNIT, -5, angle)
                _t2x, _t2y = S.rotate_point(i * _UNIT, 5, angle)
                pygame.draw.line(screen, AXIS_X_COLOR,
                    (int(px + _t1x), int(py - _t1y)),
                    (int(px + _t2x), int(py - _t2y)), 1)
                _active_dir = (not flip_x and i > 0) or (flip_x and i < 0)
                if _active_dir:
                    _np = S.rotate_point(i * _UNIT, 14, angle)
                    screen.blit(font_small.render(str(i), True, AXIS_X_COLOR),
                        (int(px + _np[0]) - 4, int(py - _np[1]) - 7))
            # засечки и числа на оси Y
            for i in range(-_num_units, _num_units + 1):
                if i == 0:
                    continue
                _t1x, _t1y = S.rotate_point(-5, i * _UNIT, angle)
                _t2x, _t2y = S.rotate_point(5, i * _UNIT, angle)
                pygame.draw.line(screen, AXIS_Y_COLOR,
                    (int(px + _t1x), int(py - _t1y)),
                    (int(px + _t2x), int(py - _t2y)), 1)
                if i > 0:
                    _np = S.rotate_point(14, i * _UNIT, angle)
                    screen.blit(font_small.render(str(i), True, AXIS_Y_COLOR),
                        (int(px + _np[0]) - 4, int(py - _np[1]) - 7))

        for idx, p in enumerate(players):
            color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
            if not p["alive"]:
                color = (70, 70, 70)
            cx, cy = int(p["x"]), int(p["y"])
            pygame.draw.circle(screen, color, (cx, cy), S.PLAYER_RADIUS)
            border = (255, 255, 255) if p["id"] == turn_id and p["alive"] else (10, 10, 10)
            pygame.draw.circle(screen, border, (cx, cy), S.PLAYER_RADIUS, width=2)
            label = f"{p['name']}"
            you = "  (вы)" if p["id"] == net.my_id else ""
            screen.blit(font_small.render(label + you, True, TEXT_COLOR), (cx - 30, cy - S.PLAYER_RADIUS - 44))
            # hp bar
            bw = 40
            pygame.draw.rect(screen, HP_BG, (cx - bw // 2, cy - S.PLAYER_RADIUS - 16, bw, 6))
            hp_w = int(bw * max(0, p["hp"]) / S.MAX_HP)
            pygame.draw.rect(screen, HP_COLOR, (cx - bw // 2, cy - S.PLAYER_RADIUS - 16, hp_w, 6))
            hp_txt = f"{p['hp']}/{S.MAX_HP}"
            hp_surf = font_small.render(hp_txt, True, HP_COLOR)
            screen.blit(hp_surf, (cx - hp_surf.get_width() // 2, cy - S.PLAYER_RADIUS - 28))
            if p.get("bonus_effect"):
                eff_txt = {"damage": "DMG", "shield": "SHD", "double_shot": "x2", "angle_reset": "ANG"}.get(p["bonus_effect"], "?")
                eff_surf = font_small.render(eff_txt, True, (200, 130, 255))
                screen.blit(eff_surf, (cx + S.PLAYER_RADIUS + 4, cy - S.PLAYER_RADIUS))

        bonus = state.get("bonus")
        if bonus:
            bx, by = int(bonus["x"]), int(bonus["y"])
            hs = S.BONUS_SIZE // 2
            pulse = abs(math.sin(time.time() * 4)) * 80 + 175
            glow_color = (int(pulse), 50, int(pulse))
            pygame.draw.rect(screen, glow_color, (bx - hs - 3, by - hs - 3, S.BONUS_SIZE + 6, S.BONUS_SIZE + 6), width=2)
            pygame.draw.rect(screen, (180, 60, 255), (bx - hs, by - hs, S.BONUS_SIZE, S.BONUS_SIZE), width=2)
            btype = bonus.get("type", "")
            blabel = {"damage": "DMG", "shield": "SHD", "double_shot": "x2", "angle_reset": "ANG"}.get(btype, "?")
            bsurf = font_small.render(blabel, True, (200, 130, 255))
            screen.blit(bsurf, (bx - bsurf.get_width() // 2, by - 7))

        # анимация полёта снаряда
        now = time.time()
        if anim_active and anim_points:
            elapsed = now - last_anim_time
            target_idx = min(len(anim_points), int(elapsed * 220))
            anim_idx = max(anim_idx, target_idx)
            shown = anim_points[:max(2, anim_idx)]
            if len(shown) >= 2:
                pygame.draw.lines(screen, TRAJ_COLOR, False, shown, 2)
            if anim_idx >= len(anim_points):
                if anim_result and anim_result.get("pos"):
                    pos = anim_result["pos"]
                    kind = anim_result.get("kind")
                    if kind in ("obstacle", "player"):
                        pygame.draw.circle(screen, EXPLOSION_COLOR, (int(pos[0]), int(pos[1])), 18, width=3)
                    elif kind == "shield":
                        pygame.draw.circle(screen, (100, 180, 255), (int(pos[0]), int(pos[1])), 18, width=3)
                if elapsed * 220 > len(anim_points) + 60:
                    anim_active = False
        elif anim_points and len(anim_points) >= 2:
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

        # поле ввода
        can_fire = state.get("started") and turn_id == net.my_id and not game_over_msg
        input_box.draw(screen)
        btn_color = (60, 140, 70) if can_fire else (60, 60, 60)
        pygame.draw.rect(screen, btn_color, fire_btn, border_radius=6)
        screen.blit(font.render("Огонь!", True, TEXT_COLOR), (fire_btn.x + 30, fire_btn.y + 7))
        flip_color = (80, 120, 180) if can_fire else (60, 60, 60)
        pygame.draw.rect(screen, flip_color, flip_btn, border_radius=6)
        flip_label = "\u2190" if flip_x else "\u2192"
        screen.blit(font.render(flip_label, True, TEXT_COLOR), (flip_btn.x + 30, flip_btn.y + 7))

        if _active:
            mx, my = pygame.mouse.get_pos()
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

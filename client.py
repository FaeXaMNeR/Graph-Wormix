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
        txt = self.text if self.text else "y(x) = ... (например sin(x)*30)"
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
    screen = pygame.display.set_mode((S.WIDTH, S.HEIGHT))
    pygame.display.set_caption("Артиллерия по сети — %s" % name)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)
    font_big = pygame.font.SysFont("consolas", 26, bold=True)
    font_small = pygame.font.SysFont("consolas", 14)

    try:
        net = NetClient(host, port, name)
    except Exception as e:
        print("Не удалось подключиться к серверу:", e)
        sys.exit(1)

    input_box = TextInputBox((20, S.HEIGHT - 50, 900, 36), font)
    fire_btn = pygame.Rect(940, S.HEIGHT - 50, 140, 36)
    flip_btn = pygame.Rect(1090, S.HEIGHT - 50, 80, 36)
    flip_x = False

    state = {"started": False, "round": 1, "turn": None, "players": [], "log": ""}
    grid_surface = None
    grid_b64_cache = None
    error_msg = ""
    error_until = 0
    game_over_msg = None
    choosing_spawn = False

    # анимация полёта снаряда
    anim_points = []
    anim_idx = 0
    anim_active = False
    anim_result = None
    last_anim_time = 0

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
            else:
                res = input_box.handle_event(ev)
                if res == "submit" or (ev.type == pygame.MOUSEBUTTONDOWN and fire_btn.collidepoint(ev.pos)):
                    if input_box.text.strip():
                        net.send({"type": "fire", "expr": input_box.text.strip(), "flip": flip_x})
                        input_box.text = ""
                if ev.type == pygame.MOUSEBUTTONDOWN and flip_btn.collidepoint(ev.pos):
                    flip_x = not flip_x

        for msg in net.poll():
            t = msg.get("type")
            if t == "welcome":
                net.my_id = msg["id"]
            elif t == "state":
                state = msg
                if msg.get("started"):
                    choosing_spawn = False
                if msg.get("grid_b64") != grid_b64_cache:
                    grid_b64_cache = msg.get("grid_b64")
                    grid_surface = rebuild_grid_surface(grid_b64_cache)
            elif t == "shot":
                anim_points = [tuple(p) for p in msg["points"]]
                anim_idx = 0
                anim_active = True
                anim_result = msg["result"]
                last_anim_time = time.time()
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
                else:
                    game_over_msg = "Все игроки выбыли. Без победителя."
            elif t == "choose_spawn":
                choosing_spawn = True
                game_over_msg = None
                if msg.get("grid_b64"):
                    grid_b64_cache = msg["grid_b64"]
                    grid_surface = rebuild_grid_surface(grid_b64_cache)

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
            screen.blit(font_small.render(label + you, True, TEXT_COLOR), (cx - 30, cy - S.PLAYER_RADIUS - 32))
            # hp bar
            bw = 40
            pygame.draw.rect(screen, HP_BG, (cx - bw // 2, cy - S.PLAYER_RADIUS - 16, bw, 6))
            hp_w = int(bw * max(0, p["hp"]) / S.MAX_HP)
            pygame.draw.rect(screen, HP_COLOR, (cx - bw // 2, cy - S.PLAYER_RADIUS - 16, hp_w, 6))

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
            screen.blit(txt, (S.WIDTH // 2 - txt.get_width() // 2, S.HEIGHT // 2 - 20))

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

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()

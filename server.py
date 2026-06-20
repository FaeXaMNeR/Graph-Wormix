# -*- coding: utf-8 -*-
"""
Сервер для сетевой артиллерийской игры.

Запуск:
    python3 server.py [--port 5555] [--players 2]

Протокол: TCP, по строке JSON на сообщение (newline-delimited).

От клиента к серверу:
    {"type": "join", "name": "..."}
    {"type": "fire", "expr": "sin(x)*30"}

От сервера к клиенту:
    {"type": "welcome", "id": <int>}
    {"type": "state", "round": int, "turn": <id|null>, "players": [...],
     "grid_b64": "...", "log": "..."}
    {"type": "shot", "shooter": <id>, "expr": "...", "points": [[x,y],...],
     "result": {"kind": "player"/"obstacle"/"miss", "target": <id|null>, "pos": [x,y]}}
    {"type": "error", "msg": "..."}
    {"type": "game_over", "winner": <id|null>}
"""
import argparse
import base64
import json
import math
import random
import socket
import threading
import time

import shared as S


class Player:
    def __init__(self, pid, name, conn, addr):
        self.id = pid
        self.name = name
        self.conn = conn
        self.addr = addr
        self.wfile = conn.makefile("w", encoding="utf-8", newline="\n")
        self.rfile = conn.makefile("r", encoding="utf-8", newline="\n")
        self.hp = S.MAX_HP
        self.x = 0.0
        self.y = 0.0
        self.angle = 0.0
        self.alive = True
        self.lock = threading.Lock()
        self.bonus_effect = None       # активный эффект бонуса
        self.bonus_double_used = False # для double_shot: был ли первый выстрел
        self.moved_this_turn = False   # переместился ли в этом ходу

    def send(self, msg):
        try:
            with self.lock:
                self.wfile.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.wfile.flush()
        except Exception:
            pass


class GameServer:
    def __init__(self, port, target_players, bind_host="0.0.0.0", turn_timeout=None):
        self.port = port
        self.target_players = target_players
        self.bind_host = bind_host
        self.turn_timeout = turn_timeout if turn_timeout is not None else S.TURN_TIMEOUT
        self.players = {}             # id -> Player
        self.next_id = 1
        self.order = []               # turn order (list of ids), alive only, rotated each shot
        self.turn_idx = 0
        self.round_no = 1
        self.grid = [[False] * S.GRID_W for _ in range(S.GRID_H)]
        self.started = False
        self.game_over = False
        self.lock = threading.RLock()
        self.log_lines = []
        self.spawn_positions = {}     # id -> (x, y)
        self.spawn_attempts = 0
        self.restart_votes = set()
        self._turn_timer = None
        self.bonus = None              # {"type": str, "x": float, "y": float} or None
        self._last_bonus_type = None   # для ограничения 2 раз подряд

    # ---------------- Поле и препятствия ----------------

    def gen_obstacles(self, target_density=None):
        if target_density is None:
            target_density = S.OBSTACLE_FILL
        total_cells = S.GRID_W * S.GRID_H
        target = int(total_cells * target_density)
        grid = [[False] * S.GRID_W for _ in range(S.GRID_H)]
        filled = 0
        for _ in range(500):
            if filled >= target:
                break
            kind = random.choice(["square", "circle", "rect"])
            cx = random.randint(0, S.WIDTH)
            cy = random.randint(0, S.HEIGHT)
            if kind == "square":
                size = random.randint(40, 160)
                x0 = int((cx - size / 2) // S.GRID_CELL)
                x1 = int((cx + size / 2) // S.GRID_CELL)
                y0 = int((cy - size / 2) // S.GRID_CELL)
                y1 = int((cy + size / 2) // S.GRID_CELL)
                for gy in range(max(0, y0), min(S.GRID_H, y1 + 1)):
                    for gx in range(max(0, x0), min(S.GRID_W, x1 + 1)):
                        if not grid[gy][gx]:
                            grid[gy][gx] = True
                            filled += 1
            elif kind == "circle":
                r = random.randint(30, 100)
                x0 = int((cx - r) // S.GRID_CELL)
                x1 = int((cx + r) // S.GRID_CELL)
                y0 = int((cy - r) // S.GRID_CELL)
                y1 = int((cy + r) // S.GRID_CELL)
                for gy in range(max(0, y0), min(S.GRID_H, y1 + 1)):
                    for gx in range(max(0, x0), min(S.GRID_W, x1 + 1)):
                        px = gx * S.GRID_CELL + S.GRID_CELL / 2
                        py = gy * S.GRID_CELL + S.GRID_CELL / 2
                        if (px - cx) ** 2 + (py - cy) ** 2 <= r * r and not grid[gy][gx]:
                            grid[gy][gx] = True
                            filled += 1
            else:
                w = random.randint(40, 200)
                h = random.randint(40, 140)
                x0 = int((cx - w / 2) // S.GRID_CELL)
                x1 = int((cx + w / 2) // S.GRID_CELL)
                y0 = int((cy - h / 2) // S.GRID_CELL)
                y1 = int((cy + h / 2) // S.GRID_CELL)
                for gy in range(max(0, y0), min(S.GRID_H, y1 + 1)):
                    for gx in range(max(0, x0), min(S.GRID_W, x1 + 1)):
                        if not grid[gy][gx]:
                            grid[gy][gx] = True
                            filled += 1
        self.grid = grid

    def cell_free(self, gx, gy):
        if gx < 0 or gy < 0 or gx >= S.GRID_W or gy >= S.GRID_H:
            return False
        return not self.grid[gy][gx]

    def find_spawn(self, existing):
        margin = 60
        min_dist_sq = (2 * S.GRID_UNIT_PX) ** 2
        for _ in range(500):
            x = random.uniform(margin, S.WIDTH - margin)
            y = random.uniform(margin, S.HEIGHT - margin)
            gx, gy = int(x // S.GRID_CELL), int(y // S.GRID_CELL)
            ok = True
            for dgy in range(-3, 4):
                for dgx in range(-3, 4):
                    if not self.cell_free(gx + dgx, gy + dgy):
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            too_close = False
            for (ex, ey) in existing:
                if (ex - x) ** 2 + (ey - y) ** 2 < min_dist_sq:
                    too_close = True
                    break
            if too_close:
                continue
            return x, y
        return S.WIDTH / 2, S.HEIGHT / 2

    # ---------------- Жизненный цикл партии ----------------

    def log(self, text):
        print("[LOG]", text)
        self.log_lines.append(text)
        self.log_lines = self.log_lines[-50:]

    def start_game(self):
        with self.lock:
            if self.started or not self.players:
                return
            self.gen_obstacles()
            self.spawn_positions = {}
            self.spawn_attempts = 0
            grid_bytes = S.grid_to_bytes(self.grid)
            grid_b64 = base64.b64encode(grid_bytes).decode("ascii")
            msg = {
                "type": "choose_spawn",
                "grid_b64": grid_b64,
                "players": [
                    {"id": p.id, "name": p.name}
                    for p in self.players.values()
                ],
            }
            for p in list(self.players.values()):
                p.send(msg)
            self.log("Выберите стартовую позицию на поле.")

    def _reset_all_spawns(self, msg_text):
        self.spawn_positions = {}
        self.broadcast({"type": "error", "msg": msg_text})
        grid_bytes = S.grid_to_bytes(self.grid)
        grid_b64 = base64.b64encode(grid_bytes).decode("ascii")
        for p in list(self.players.values()):
            p.send({"type": "choose_spawn", "grid_b64": grid_b64,
                     "players": [{"id": pp.id, "name": pp.name} for pp in self.players.values()]})

    def handle_spawn(self, p, x, y):
        with self.lock:
            if self.started or p.id in self.spawn_positions:
                return
            x = max(20, min(S.WIDTH - 20, float(x)))
            y = max(20, min(S.HEIGHT - 20, float(y)))
            gx, gy = int(x // S.GRID_CELL), int(y // S.GRID_CELL)
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                self.log(f"{p.name}: нельзя стоять на препятствии! Расстановка сброшена.")
                self._reset_all_spawns(f"{p.name} встал на препятствие — расстановка сброшена.")
                return
            self.spawn_positions[p.id] = (x, y)
            self.log(f"{p.name} выбрал позицию.")
            ready = [pid for pid in self.players if pid in self.spawn_positions]
            if len(ready) >= len(self.players):
                self._begin_game()

    def _begin_game(self):
        min_dist_sq = (2 * S.GRID_UNIT_PX) ** 2
        positions = list(self.spawn_positions.values())
        if len(positions) >= 2:
            too_close = False
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    ax, ay = positions[i]
                    bx, by = positions[j]
                    if (ax - bx) ** 2 + (ay - by) ** 2 < min_dist_sq:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                self.spawn_attempts += 1
                self.log(f"Слишком близко! Попытка {self.spawn_attempts}/3.")
                if self.spawn_attempts >= 3:
                    self.log("3 попытки исчерпаны — авто-расстановка.")
                    self.spawn_attempts = 0
                    self._auto_spawn()
                    return
                self._reset_all_spawns(f"Слишком близко! Попытка {self.spawn_attempts}/3. Выберите заново.")
                return
        self._finalize_start()

    def _finalize_start(self):
        for p in self.players.values():
            x, y = self.spawn_positions.get(p.id, (S.WIDTH / 2, S.HEIGHT / 2))
            p.x, p.y = x, y
            p.hp = S.MAX_HP
            p.alive = True
            p.angle = random.uniform(-math.pi, math.pi)
            p.bonus_effect = None
            p.bonus_double_used = False
            p.moved_this_turn = False
        self.order = list(self.players.keys())
        random.shuffle(self.order)
        self.turn_idx = 0
        self.round_no = 1
        self.started = True
        self.game_over = False
        self.spawn_attempts = 0
        self.spawn_positions = {}
        self.bonus = None
        self._last_bonus_type = None
        self.log("Игра началась! Игроков: %d" % len(self.players))
        self._start_turn_timer()
        self.broadcast_state()

    def _auto_spawn(self):
        existing = []
        auto_pos = {}
        for p in self.players.values():
            x, y = self.find_spawn(existing)
            auto_pos[p.id] = (x, y)
            existing.append((x, y))
        self.spawn_positions = auto_pos
        self.log("Авто-расстановка выполнена.")
        self._finalize_start()

    def alive_ids_in_order(self):
        return [pid for pid in self.order if pid in self.players and self.players[pid].alive]

    def current_turn_id(self):
        ids = self.alive_ids_in_order()
        if not ids:
            return None
        self.turn_idx %= len(ids)
        return ids[self.turn_idx]

    def _start_turn_timer(self):
        self._cancel_turn_timer()
        self._turn_started_at = time.time()
        self._turn_timer = threading.Timer(self.turn_timeout, self._on_turn_timeout)
        self._turn_timer.daemon = True
        self._turn_timer.start()

    def _cancel_turn_timer(self):
        if self._turn_timer is not None:
            self._turn_timer.cancel()
        self._turn_timer = None
        self._turn_started_at = 0

    def _on_turn_timeout(self):
        with self.lock:
            if not self.started or self.game_over:
                return
            pid = self.current_turn_id()
            if pid and pid in self.players:
                self.log(f"Время вышло — ход {self.players[pid].name} пропущен.")
            self.advance_turn()
            self._start_turn_timer()
            self.broadcast_state()

    def advance_turn(self):
        ids = self.alive_ids_in_order()
        if len(ids) <= 1:
            return
        self.turn_idx = (self.turn_idx + 1) % len(ids)
        next_pid = ids[self.turn_idx]
        if next_pid in self.players:
            self.players[next_pid].moved_this_turn = False
        if self.turn_idx == 0:
            self.round_no += 1
            for pid in ids:
                pp = self.players[pid]
                if pp.bonus_effect == "angle_reset":
                    pp.angle = 0.0
                    pp.bonus_effect = None
                    self.log(f"{pp.name}: сброс угла до 0°!")
                else:
                    pp.angle = random.uniform(-math.pi, math.pi)
            self.log("Раунд %d: новые углы координатных систем." % self.round_no)
            if self.round_no >= 2 and self.round_no % 2 == 0 and self.bonus is None:
                self._spawn_bonus()

    def _spawn_bonus(self):
        for _ in range(200):
            x = random.uniform(S.BONUS_SIZE, S.WIDTH - S.BONUS_SIZE)
            y = random.uniform(S.BONUS_SIZE, S.HEIGHT - S.BONUS_SIZE)
            gx, gy = int(x // S.GRID_CELL), int(y // S.GRID_CELL)
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                continue
            too_close = False
            for p in self.players.values():
                if (p.x - x) ** 2 + (p.y - y) ** 2 < (2 * S.GRID_UNIT_PX) ** 2:
                    too_close = True
                    break
            if too_close:
                continue
            available = [t for t in S.BONUS_TYPES if t != self._last_bonus_type]
            btype = random.choice(available)
            self.bonus = {"type": btype, "x": x, "y": y}
            self._last_bonus_type = btype
            self.log(f"Бонус появился: {btype}!")
            return

    def _try_pickup_bonus(self, px, py):
        if self.bonus is None:
            return
        bx, by = self.bonus["x"], self.bonus["y"]
        if (px - bx) ** 2 + (py - by) ** 2 <= S.BONUS_PICKUP_RADIUS ** 2:
            self._apply_bonus(self.bonus["type"], self.current_turn_id())
            self.bonus = None

    def _try_shot_bonus(self, wx, wy):
        if self.bonus is None:
            return
        bx, by = self.bonus["x"], self.bonus["y"]
        if (wx - bx) ** 2 + (wy - by) ** 2 <= S.BONUS_PICKUP_RADIUS ** 2:
            self._apply_bonus(self.bonus["type"], self.current_turn_id())
            self.bonus = None

    def _apply_bonus(self, btype, pid):
        p = self.players.get(pid)
        if not p:
            return
        p.bonus_effect = btype
        self.log(f"{p.name} подобрал бонус: {btype}!")

    # ---------------- Трассировка снаряда ----------------

    def simulate_shot(self, shooter, fn, flip=False):
        points = [(shooter.x, shooter.y)]
        result = {"kind": "miss", "target": None, "pos": None}
        step_grid = S.TRAJ_STEP / S.GRID_UNIT_PX
        max_grid = S.TRAJ_MAXX / S.GRID_UNIT_PX
        steps = int(max_grid / step_grid)
        dmg = 2 if shooter.bonus_effect == "damage" else 1
        if shooter.bonus_effect == "damage":
            shooter.bonus_effect = None
        x = 0.0
        prev_wx, prev_wy = shooter.x, shooter.y
        arc_len_px = 0.0
        for _ in range(steps):
            x_eval = -x if flip else x
            try:
                y = fn(x_eval)
            except Exception:
                break
            if not math.isfinite(y):
                break
            h = 0.01
            try:
                y_plus = fn(x_eval + h)
                y_minus = fn(x_eval - h)
                dy_dx = (y_plus - y_minus) / (2 * h)
            except Exception:
                dy_dx = 0.0
            tangent_len = math.sqrt(1 + dy_dx * dy_dx)
            nx_local = -dy_dx / tangent_len
            ny_local = 1.0 / tangent_len
            A_d = S.NOISE_A0 + S.NOISE_ALPHA * arc_len_px
            offset = random.gauss(0, A_d) / S.GRID_UNIT_PX
            x_noisy = x_eval + nx_local * offset
            y_noisy = y + ny_local * offset
            px_local = x_noisy * S.GRID_UNIT_PX
            py_local = y_noisy * S.GRID_UNIT_PX
            dx, dy = S.rotate_point(px_local, py_local, shooter.angle)
            wx = shooter.x + dx
            wy = shooter.y - dy
            points.append((wx, wy))
            arc_len_px += math.sqrt((wx - prev_wx) ** 2 + (wy - prev_wy) ** 2)

            if wx < 0 or wx >= S.WIDTH:
                prev_wx, prev_wy = wx, wy
                break

            self._try_shot_bonus(wx, wy)

            ax, ay = prev_wx, prev_wy
            bx, by = wx, wy
            seg_dx, seg_dy = bx - ax, by - ay
            seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy

            hit_someone = False
            hit_pos = None
            hit_other = None
            best_t = 2.0
            for other in self.players.values():
                if not other.alive or other.id == shooter.id:
                    continue
                if seg_len_sq < 1e-10:
                    d2 = (other.x - bx) ** 2 + (other.y - by) ** 2
                    t = 0.0
                else:
                    t = max(0.0, min(1.0, ((other.x - ax) * seg_dx + (other.y - ay) * seg_dy) / seg_len_sq))
                    cx = ax + t * seg_dx
                    cy = ay + t * seg_dy
                    d2 = (other.x - cx) ** 2 + (other.y - cy) ** 2
                if d2 <= S.PLAYER_RADIUS ** 2 and t < best_t:
                    best_t = t
                    hit_other = other
                    if seg_len_sq < 1e-10:
                        hit_pos = (bx, by)
                    else:
                        cx = ax + t * seg_dx
                        cy = ay + t * seg_dy
                        hit_pos = (cx, cy)
            if hit_other:
                other = hit_other
                wx, wy = hit_pos
                if points:
                    points[-1] = (wx, wy)
                if other.bonus_effect == "shield":
                    other.bonus_effect = None
                    result = {"kind": "shield", "target": other.id, "pos": [wx, wy]}
                    self.log(f"{other.name} заблокировал попадание щитом!")
                else:
                    other.hp -= dmg
                    if other.hp <= 0:
                        other.alive = False
                    result = {"kind": "player", "target": other.id, "pos": [wx, wy]}
                    self.log(f"{shooter.name} попал по {other.name}! HP {other.name}: {max(other.hp,0)}")
                hit_someone = True
            if hit_someone:
                break

            gx, gy = int(bx // S.GRID_CELL), int(by // S.GRID_CELL)
            hit_obstacle = False
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                hit_obstacle = True
            if not hit_obstacle and seg_len_sq > 1e-10:
                n_steps = max(1, int(math.sqrt(seg_len_sq) / S.GRID_CELL))
                for si in range(1, n_steps + 1):
                    t = si / n_steps
                    sx = ax + t * seg_dx
                    sy = ay + t * seg_dy
                    gx, gy = int(sx // S.GRID_CELL), int(sy // S.GRID_CELL)
                    if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                        hit_obstacle = True
                        bx, by = sx, sy
                        break
            if hit_obstacle:
                if points:
                    points[-1] = (bx, by)
                self.explode(bx, by)
                result = {"kind": "obstacle", "target": None, "pos": [bx, by]}
                self.log(f"{shooter.name} разрушил часть препятствия.")
                break

            prev_wx, prev_wy = wx, wy
            x += step_grid

        return points, result

    def explode(self, wx, wy):
        r_cells = int(S.EXPLOSION_RADIUS / S.GRID_CELL) + 1
        gx0, gy0 = int(wx // S.GRID_CELL), int(wy // S.GRID_CELL)
        for dgy in range(-r_cells, r_cells + 1):
            for dgx in range(-r_cells, r_cells + 1):
                gx, gy = gx0 + dgx, gy0 + dgy
                if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H:
                    px = gx * S.GRID_CELL + S.GRID_CELL / 2
                    py = gy * S.GRID_CELL + S.GRID_CELL / 2
                    if (px - wx) ** 2 + (py - wy) ** 2 <= S.EXPLOSION_RADIUS ** 2:
                        self.grid[gy][gx] = False

    # ---------------- Сеть ----------------

    def broadcast_state(self):
        grid_bytes = S.grid_to_bytes(self.grid)
        grid_b64 = base64.b64encode(grid_bytes).decode("ascii")
        msg = {
            "type": "state",
            "started": self.started,
            "round": self.round_no,
            "turn": self.current_turn_id() if self.started else None,
            "turn_deadline": self._turn_started_at + self.turn_timeout if self.started else 0,
            "players": [
                {"id": p.id, "name": p.name, "x": p.x, "y": p.y,
                 "hp": p.hp, "angle": p.angle, "alive": p.alive,
                 "bonus_effect": p.bonus_effect}
                for p in self.players.values()
            ],
            "bonus": self.bonus,
            "grid_b64": grid_b64,
            "log": self.log_lines[-1] if self.log_lines else "",
        }
        for p in list(self.players.values()):
            p.send(msg)

    def broadcast(self, msg):
        for p in list(self.players.values()):
            p.send(msg)

    def check_game_over(self):
        alive = [p for p in self.players.values() if p.alive]
        if self.started and not self.game_over and len(alive) <= 1:
            self.game_over = True
            self._cancel_turn_timer()
            self.restart_votes = set()
            winner = alive[0].id if alive else None
            self.broadcast({"type": "game_over", "winner": winner})
            self.log("Игра окончена. Победитель: %s" % (alive[0].name if alive else "никто"))

    def handle_restart_vote(self, p):
        with self.lock:
            if not self.game_over:
                return
            self.restart_votes.add(p.id)
            names = [self.players[pid].name for pid in self.restart_votes if pid in self.players]
            self.log(f"{p.name} хочет реванш ({len(self.restart_votes)}/{len(self.players)}).")
            if len(self.restart_votes) >= len(self.players):
                self.restart_votes = set()
                self.game_over = False
                self.log("Все согласны! Новый раунд.")
                self.start_game()
            else:
                self.broadcast({"type": "restart_status",
                                "voted": len(self.restart_votes),
                                "total": len(self.players)})

    def reset_game(self):
        self._cancel_turn_timer()
        self.players.clear()
        self.next_id = 1
        self.order = []
        self.turn_idx = 0
        self.round_no = 1
        self.grid = [[False] * S.GRID_W for _ in range(S.GRID_H)]
        self.started = False
        self.game_over = False
        self.bonus = None
        self._last_bonus_type = None
        self.log("Сервер сброшен. Ожидание новых игроков.")

    def _apply_turn_effects(self, p):
        if p.bonus_effect == "angle_reset":
            p.angle = 0.0
            self.log(f"{p.name}: сброс угла до 0°!")
            p.bonus_effect = None
        if p.bonus_effect == "shield":
            p.bonus_effect = None
        if p.bonus_effect == "double_shot":
            p.bonus_double_used = False

    def handle_fire(self, p, expr, flip=False):
        with self.lock:
            if not self.started or self.game_over:
                p.send({"type": "error", "msg": "Игра ещё не началась."})
                return
            if self.current_turn_id() != p.id:
                p.send({"type": "error", "msg": "Сейчас не ваш ход."})
                return
            if p.bonus_effect == "double_shot" and p.bonus_double_used:
                pass
            else:
                self._apply_turn_effects(p)
            try:
                fn = S.compile_function(expr)
            except S.UnsafeExpression as e:
                p.send({"type": "error", "msg": f"Некорректная функция: {e}"})
                return

            self._cancel_turn_timer()
            points, result = self.simulate_shot(p, fn, flip=flip)
            self.broadcast({
                "type": "shot",
                "shooter": p.id,
                "expr": expr,
                "points": points,
                "result": result,
            })
            advance = True
            if p.bonus_effect == "double_shot":
                if not p.bonus_double_used:
                    p.bonus_double_used = True
                    advance = False
                else:
                    p.bonus_effect = None
                    p.bonus_double_used = False
            if advance:
                self.advance_turn()
            self.check_game_over()
            if self.started and not self.game_over:
                self._start_turn_timer()
            self.broadcast_state()
            if not self.started or self.game_over:
                self._cancel_turn_timer()

    def handle_move(self, p, dx, dy):
        with self.lock:
            if not self.started or self.game_over:
                p.send({"type": "error", "msg": "Игра ещё не началась."})
                return
            if self.current_turn_id() != p.id:
                p.send({"type": "error", "msg": "Сейчас не ваш ход."})
                return
            if p.moved_this_turn:
                p.send({"type": "error", "msg": "Вы уже переместились в этом ходу."})
                return
            self._apply_turn_effects(p)
            d2 = dx * dx + dy * dy
            if d2 >= 8:
                p.send({"type": "error", "msg": "Слишком далеко! x^2 + y^2 < 8"})
                return
            local_px = dx * S.GRID_UNIT_PX
            local_py = dy * S.GRID_UNIT_PX
            wx, wy = S.rotate_point(local_px, local_py, p.angle)
            nx = p.x + wx
            ny = p.y - wy
            if nx < 0 or nx >= S.WIDTH or ny < 0 or ny >= S.HEIGHT:
                p.send({"type": "error", "msg": "Выход за границы поля!"})
                return
            gx, gy = int(nx // S.GRID_CELL), int(ny // S.GRID_CELL)
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                p.send({"type": "error", "msg": "Нельзя войти в препятствие!"})
                return
            self.log(f"{p.name} переместился на ({dx:.1f}, {dy:.1f}).")
            self._cancel_turn_timer()
            p.x, p.y = nx, ny
            p.moved_this_turn = True
            self._try_pickup_bonus(nx, ny)
            self._start_turn_timer()
            self.broadcast_state()

    def handle_end_turn(self, p):
        with self.lock:
            if not self.started or self.game_over:
                return
            if self.current_turn_id() != p.id:
                return
            self._cancel_turn_timer()
            self.advance_turn()
            self.check_game_over()
            if self.started and not self.game_over:
                self._start_turn_timer()
            self.broadcast_state()
            if not self.started or self.game_over:
                self._cancel_turn_timer()

    def client_thread(self, conn, addr):
        with self.lock:
            if self.started and not self.game_over:
                conn.close()
                return
            pid = self.next_id
            self.next_id += 1
        name = f"Player{pid}"
        p = Player(pid, name, conn, addr)
        try:
            first_line = p.rfile.readline()
            if not first_line:
                return
            data = json.loads(first_line)
            if data.get("type") == "join" and data.get("name"):
                name = str(data["name"])[:20]
                p.name = name
        except Exception:
            pass

        with self.lock:
            self.players[pid] = p
            self.log(f"{p.name} (id={pid}) подключился ({addr[0]}).")
        p.send({"type": "welcome", "id": pid})
        self.broadcast_state()

        with self.lock:
            if not self.started and len(self.players) >= self.target_players:
                threading.Timer(1.0, self.start_game).start()

        try:
            for line in p.rfile:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("type") == "fire":
                    self.handle_fire(p, msg.get("expr", ""), flip=msg.get("flip", False))
                elif msg.get("type") == "spawn":
                    self.handle_spawn(p, msg.get("x", 0), msg.get("y", 0))
                elif msg.get("type") == "move":
                    self.handle_move(p, float(msg.get("dx", 0)), float(msg.get("dy", 0)))
                elif msg.get("type") == "restart_vote":
                    self.handle_restart_vote(p)
                elif msg.get("type") == "end_turn":
                    self.handle_end_turn(p)
        except Exception:
            pass
        finally:
            with self.lock:
                if pid in self.players:
                    self.players.pop(pid)
                    self.log(f"{p.name} отключился.")
                if self.started and not self.game_over:
                    if not self.players:
                        try:
                            self.reset_game()
                        except Exception as e:
                            self.log(f"Ошибка сброса: {e}")
                    else:
                        self.check_game_over()
                        self.broadcast_state()
                elif self.game_over and not self.players:
                    try:
                        self.reset_game()
                    except Exception as e:
                        self.log(f"Ошибка сброса: {e}")
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _get_local_ip():
        """Определяет локальный IP-адрес (для LAN)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _get_public_ip():
        """Пытается определить внешний (публичный) IP-адрес."""
        import urllib.request
        services = [
            "https://api.ipify.org?format=text",
            "https://icanhazip.com",
            "https://ifconfig.me/ip",
            "https://checkip.amazonaws.com",
        ]
        for url in services:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "WormixServer/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    ip = resp.read().decode("utf-8").strip()
                    if ip and all(c in "0123456789." for c in ip):
                        return ip
            except Exception:
                continue
        return None

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_host, self.port))
        srv.listen(8)
        srv.settimeout(1.0)

        local_ip = self._get_local_ip()

        print("=" * 60)
        print("  Wormix Game Server")
        print("=" * 60)
        print(f"  Порт:           {self.port}")
        print(f"  Привязка:       {self.bind_host}")
        print(f"  Игроков:        {self.target_players}")
        print(f"  Локальный IP:   {local_ip}")
        print("=" * 60)
        print()
        print("  Подключение (на этом же компьютере):")
        print(f"    python client.py 127.0.0.1 {self.port} Имя")
        print()
        print("  Подключение (в локальной сети):")
        print(f"    python client.py {local_ip} {self.port} Имя")
        print()

        if self.bind_host == "0.0.0.0":
            public_ip = self._get_public_ip()
            if public_ip:
                print(f"  Публичный IP:   {public_ip}")
                print(f"  Через интернет:")
                print(f"    python client.py {public_ip} {self.port} Имя")
                print()
                print(f"  (не забудьте открыть порт {self.port} в файрволе/роутере)")
                print()
        print("=" * 60)
        try:
            while True:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self.client_thread, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("Остановка сервера.")
        except Exception as e:
            print(f"Критическая ошибка: {e}")
        finally:
            srv.close()


def main():
    ap = argparse.ArgumentParser(
        description="Сервер сетевой артиллерийской игры Wormix",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры запуска:
  python server.py                          # запуск на всех интерфейсах
  python server.py --local                  # только локальная сеть (127.0.0.1)
  python server.py --port 7777              # свой порт
  python server.py --turn-timeout 30        # 30 секунд на ход
  python server.py --players 4              # ждать 4 игрока перед стартом
        """
    )
    ap.add_argument("--port", type=int, default=S.PROTOCOL_PORT_DEFAULT,
                    help="порт сервера (по умолчанию: %(default)s)")
    ap.add_argument("--players", type=int, default=2,
                    help="число игроков для автостарта (2-%d, по умолчанию: %%(default)s)" % S.MAX_PLAYERS)
    ap.add_argument("--bind", type=str, default="0.0.0.0",
                    help="IP-адрес для привязки (по умолчанию: 0.0.0.0 — все интерфейсы)")
    ap.add_argument("--local", action="store_true",
                    help="только локальная сеть (привязка к 127.0.0.1, без публичного IP)")
    ap.add_argument("--turn-timeout", type=int, default=S.TURN_TIMEOUT,
                    help="время на ход в секундах (по умолчанию: %%(default)s)")
    args = ap.parse_args()
    bind_host = "127.0.0.1" if args.local else args.bind
    gs = GameServer(args.port, max(2, min(S.MAX_PLAYERS, args.players)),
                    bind_host=bind_host, turn_timeout=args.turn_timeout)
    gs.run()


if __name__ == "__main__":
    main()

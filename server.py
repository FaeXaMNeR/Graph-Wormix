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

    def send(self, msg):
        try:
            with self.lock:
                self.wfile.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self.wfile.flush()
        except Exception:
            pass


class GameServer:
    def __init__(self, port, target_players):
        self.port = port
        self.target_players = target_players
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

    # ---------------- Поле и препятствия ----------------

    def gen_obstacles(self):
        shapes = []
        n = random.randint(8, 14)
        for _ in range(n):
            kind = random.choice(["square", "circle"])
            cx = random.randint(120, S.WIDTH - 120)
            cy = random.randint(120, S.HEIGHT - 120)
            if kind == "square":
                size = random.randint(35, 80)
                shapes.append(("square", cx, cy, size))
            else:
                r = random.randint(20, 45)
                shapes.append(("circle", cx, cy, r))

        grid = [[False] * S.GRID_W for _ in range(S.GRID_H)]
        for shp in shapes:
            kind = shp[0]
            if kind == "square":
                _, cx, cy, size = shp
                half = size / 2
                x0 = int((cx - half) // S.GRID_CELL)
                x1 = int((cx + half) // S.GRID_CELL)
                y0 = int((cy - half) // S.GRID_CELL)
                y1 = int((cy + half) // S.GRID_CELL)
                for gy in range(max(0, y0), min(S.GRID_H, y1 + 1)):
                    for gx in range(max(0, x0), min(S.GRID_W, x1 + 1)):
                        grid[gy][gx] = True
            else:
                _, cx, cy, r = shp
                x0 = int((cx - r) // S.GRID_CELL)
                x1 = int((cx + r) // S.GRID_CELL)
                y0 = int((cy - r) // S.GRID_CELL)
                y1 = int((cy + r) // S.GRID_CELL)
                for gy in range(max(0, y0), min(S.GRID_H, y1 + 1)):
                    for gx in range(max(0, x0), min(S.GRID_W, x1 + 1)):
                        px = gx * S.GRID_CELL + S.GRID_CELL / 2
                        py = gy * S.GRID_CELL + S.GRID_CELL / 2
                        if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                            grid[gy][gx] = True
        self.grid = grid

    def cell_free(self, gx, gy):
        if gx < 0 or gy < 0 or gx >= S.GRID_W or gy >= S.GRID_H:
            return False
        return not self.grid[gy][gx]

    def find_spawn(self, existing):
        margin = 60
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
                if (ex - x) ** 2 + (ey - y) ** 2 < (S.PLAYER_RADIUS * 6) ** 2:
                    too_close = True
                    break
            if too_close:
                continue
            return x, y
        # fallback
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

    def handle_spawn(self, p, x, y):
        with self.lock:
            if self.started or p.id in self.spawn_positions:
                return
            x = max(20, min(S.WIDTH - 20, float(x)))
            y = max(20, min(S.HEIGHT - 20, float(y)))
            gx, gy = int(x // S.GRID_CELL), int(y // S.GRID_CELL)
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                p.send({"type": "error", "msg": "Нельзя стоять на препятствии!"})
                return
            for op in self.players.values():
                if op.id == p.id:
                    continue
                if op.id in self.spawn_positions:
                    ox, oy = self.spawn_positions[op.id]
                    if (ox - x) ** 2 + (oy - y) ** 2 < (S.PLAYER_RADIUS * 6) ** 2:
                        p.send({"type": "error", "msg": "Слишком близко к другому игроку!"})
                        return
            self.spawn_positions[p.id] = (x, y)
            self.log(f"{p.name} выбрал позицию.")
            ready = [pid for pid in self.players if pid in self.spawn_positions]
            if len(ready) >= len(self.players):
                self._begin_game()

    def _begin_game(self):
        for p in self.players.values():
            x, y = self.spawn_positions.get(p.id, (S.WIDTH / 2, S.HEIGHT / 2))
            p.x, p.y = x, y
            p.hp = S.MAX_HP
            p.alive = True
            p.angle = random.uniform(-math.pi, math.pi)
        self.order = list(self.players.keys())
        random.shuffle(self.order)
        self.turn_idx = 0
        self.round_no = 1
        self.started = True
        self.game_over = False
        self.spawn_positions = {}
        self.log("Игра началась! Игроков: %d" % len(self.players))
        self.broadcast_state()

    def alive_ids_in_order(self):
        return [pid for pid in self.order if pid in self.players and self.players[pid].alive]

    def current_turn_id(self):
        ids = self.alive_ids_in_order()
        if not ids:
            return None
        self.turn_idx %= len(ids)
        return ids[self.turn_idx]

    def advance_turn(self):
        ids = self.alive_ids_in_order()
        if len(ids) <= 1:
            return
        self.turn_idx = (self.turn_idx + 1) % len(ids)
        if self.turn_idx == 0:
            self.round_no += 1
            for pid in ids:
                self.players[pid].angle = random.uniform(-math.pi, math.pi)
            self.log("Раунд %d: новые углы координатных систем." % self.round_no)

    # ---------------- Трассировка снаряда ----------------

    def simulate_shot(self, shooter, fn, flip=False):
        points = [(shooter.x, shooter.y)]
        result = {"kind": "miss", "target": None, "pos": None}
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
            dx, dy = S.rotate_point(px_local, py_local, shooter.angle)
            wx = shooter.x + dx
            wy = shooter.y - dy
            points.append((wx, wy))

            if wx < 0 or wx >= S.WIDTH or wy < 0 or wy >= S.HEIGHT:
                break

            # столкновение с игроком (включая себя)
            hit_someone = False
            for other in self.players.values():
                if not other.alive:
                    continue
                if other.id == shooter.id:
                    continue
                d2 = (other.x - wx) ** 2 + (other.y - wy) ** 2
                if d2 <= S.PLAYER_RADIUS ** 2:
                    other.hp -= 1
                    if other.hp <= 0:
                        other.alive = False
                    result = {"kind": "player", "target": other.id, "pos": [wx, wy]}
                    self.log(f"{shooter.name} попал по {other.name}! HP {other.name}: {max(other.hp,0)}")
                    hit_someone = True
                    break
            if hit_someone:
                break

            # столкновение с препятствием
            gx, gy = int(wx // S.GRID_CELL), int(wy // S.GRID_CELL)
            if 0 <= gx < S.GRID_W and 0 <= gy < S.GRID_H and self.grid[gy][gx]:
                self.explode(wx, wy)
                result = {"kind": "obstacle", "target": None, "pos": [wx, wy]}
                self.log(f"{shooter.name} разрушил часть препятствия.")
                break

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
            "players": [
                {"id": p.id, "name": p.name, "x": p.x, "y": p.y,
                 "hp": p.hp, "angle": p.angle, "alive": p.alive}
                for p in self.players.values()
            ],
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
            winner = alive[0].id if alive else None
            self.broadcast({"type": "game_over", "winner": winner})
            self.log("Игра окончена. Победитель: %s" % (alive[0].name if alive else "никто"))

    def reset_game(self):
        self.players.clear()
        self.next_id = 1
        self.order = []
        self.turn_idx = 0
        self.round_no = 1
        self.grid = [[False] * S.GRID_W for _ in range(S.GRID_H)]
        self.started = False
        self.game_over = False
        self.log("Сервер сброшен. Ожидание новых игроков.")

    def handle_fire(self, p, expr, flip=False):
        with self.lock:
            if not self.started or self.game_over:
                p.send({"type": "error", "msg": "Игра ещё не началась."})
                return
            if self.current_turn_id() != p.id:
                p.send({"type": "error", "msg": "Сейчас не ваш ход."})
                return
            try:
                fn = S.compile_function(expr)
            except S.UnsafeExpression as e:
                p.send({"type": "error", "msg": f"Некорректная функция: {e}"})
                return

            points, result = self.simulate_shot(p, fn, flip=flip)
            self.broadcast({
                "type": "shot",
                "shooter": p.id,
                "expr": expr,
                "points": points,
                "result": result,
            })
            self.advance_turn()
            self.check_game_over()
            self.broadcast_state()

    def client_thread(self, conn, addr):
        with self.lock:
            if self.started and not self.game_over:
                conn.close()
                return
            if self.game_over:
                self.reset_game()
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
        except Exception:
            pass
        finally:
            with self.lock:
                if pid in self.players:
                    self.players.pop(pid)
                    self.log(f"{p.name} отключился.")
                if self.started:
                    if not self.players:
                        try:
                            self.reset_game()
                        except Exception as e:
                            self.log(f"Ошибка сброса: {e}")
                    else:
                        self.check_game_over()
                        self.broadcast_state()
            try:
                conn.close()
            except Exception:
                pass

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(8)
        srv.settimeout(1.0)
        print(f"Сервер запущен на порту {self.port}. Жду {self.target_players} игрок(ов)...")
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=S.PROTOCOL_PORT_DEFAULT)
    ap.add_argument("--players", type=int, default=2, help="число игроков для автостарта")
    args = ap.parse_args()
    gs = GameServer(args.port, max(2, min(S.MAX_PLAYERS, args.players)))
    gs.run()


if __name__ == "__main__":
    main()

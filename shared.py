# -*- coding: utf-8 -*-
"""
Общие константы и утилиты для сетевой игры "Worms-like artillery".
"""
import ast
import math
import struct

# ---------- Геометрия поля ----------
WIDTH, HEIGHT = 1600, 900           # окно 900p
GRID_CELL = 8                       # размер ячейки сетки препятствий, px
GRID_W = WIDTH // GRID_CELL         # 160
GRID_H = HEIGHT // GRID_CELL        # 90

# ---------- Игровые параметры ----------
PLAYER_RADIUS = 14                  # радиус кружочка игрока, px (мировые координаты)
MAX_HP = 3
EXPLOSION_RADIUS = 26               # радиус разрушения препятствия, px
TRAJ_STEP = 2.0                     # шаг по локальному x при трассировке снаряда
TRAJ_MAXX = 1400.0                  # максимальная дальность по локальному x
GRID_UNIT_PX = 100                  # пикселей на единицу функции / сетки
MAX_PLAYERS = 6

PROTOCOL_PORT_DEFAULT = 5555


# ====================================================================
#  Безопасный парсер формулы y = f(x)
# ====================================================================

_ALLOWED_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": math.log, "log10": math.log10,
    "sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
    "pow": pow,
}
_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e}

_ALLOWED_NODE_TYPES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
    ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.USub, ast.UAdd, ast.FloorDiv,
)


class UnsafeExpression(Exception):
    pass


def compile_function(expr_str):
    """
    Принимает строку вида "sin(x)*40 + x/3" и возвращает безопасную
    функцию f(x) -> float. Разрешены только базовые арифметические
    операции и функции из _ALLOWED_FUNCS / константы из _ALLOWED_CONSTS.
    Бросает UnsafeExpression при недопустимом синтаксисе.
    """
    expr_str = (expr_str or "").strip()
    if not expr_str:
        raise UnsafeExpression("пустое выражение")
    if len(expr_str) > 300:
        raise UnsafeExpression("слишком длинное выражение")
    try:
        tree = ast.parse(expr_str, mode="eval")
    except SyntaxError as e:
        raise UnsafeExpression(f"синтаксическая ошибка: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id != "x" and node.id not in _ALLOWED_FUNCS and node.id not in _ALLOWED_CONSTS:
                raise UnsafeExpression(f"неизвестное имя: {node.id}")
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise UnsafeExpression("разрешены только встроенные функции (sin, cos, sqrt, ...)")
            continue
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise UnsafeExpression(f"недопустимая конструкция: {type(node).__name__}")

    code = compile(tree, "<traj>", "eval")
    safe_globals = {"__builtins__": {}}
    safe_globals.update(_ALLOWED_FUNCS)
    safe_globals.update(_ALLOWED_CONSTS)

    def f(x):
        env = dict(safe_globals)
        env["x"] = x
        return float(eval(code, {"__builtins__": {}}, env))

    # пробный вызов, чтобы сразу отсечь явный мусор (напр. неопределённые имена поймает выше)
    try:
        f(1.0)
    except ZeroDivisionError:
        pass
    except UnsafeExpression:
        raise
    except Exception as e:
        raise UnsafeExpression(f"ошибка вычисления: {e}")

    return f


def rotate_point(x, y, angle):
    ca, sa = math.cos(angle), math.sin(angle)
    return x * ca - y * sa, x * sa + y * ca


# ====================================================================
#  Упаковка сетки препятствий в компактные байты (1 байт на ячейку 0/1)
# ====================================================================

def grid_to_bytes(grid):
    flat = bytearray(GRID_W * GRID_H)
    i = 0
    for row in grid:
        for v in row:
            flat[i] = 1 if v else 0
            i += 1
    return bytes(flat)


def bytes_to_grid(b):
    grid = []
    idx = 0
    for _ in range(GRID_H):
        row = [bool(v) for v in b[idx:idx + GRID_W]]
        idx += GRID_W
        grid.append(row)
    return grid

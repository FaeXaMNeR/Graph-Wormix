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
MAX_HP = 5
EXPLOSION_RADIUS = 50               # радиус разрушения препятствия, px
TRAJ_STEP = 2.0                     # шаг по локальному x при трассировке снаряда
TRAJ_MAXX = 1400.0                  # максимальная дальность по локальному x
GRID_UNIT_PX = 100                  # пикселей на единицу функции / сетки
MAX_PLAYERS = 6
OBSTACLE_FILL = 0.30                 # доля заполнения поля препятствиями (0.0 – 1.0), default = 0.30
NOISE_A0 = 1.0                       # начальная амплитуда шума, px
NOISE_ALPHA = 0.01                   # скорость роста амплитуды, px/px
TURN_TIMEOUT = 60                    # секунд на ход

# ---------- Бонусы ----------
BONUS_TYPES = ["damage", "shield", "double_shot", "angle_reset"]
BONUS_SIZE = 16                      # ширина квадрата бонуса, px
BONUS_PICKUP_RADIUS = 30             # радиус подбора бонуса выстрелом, px

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


def _has_variable(node):
    """Проверяет, содержит ли AST-дерево переменную x."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == "x":
            return True
    return False


def _is_linear_ast(node):
    """Проверяет, что выражение линейное по x (нет вызовов функций, нет степеней)."""
    if isinstance(node, ast.Expression):
        return _is_linear_ast(node.body)
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id == "x"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_linear_ast(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        return _is_linear_ast(node.left) and _is_linear_ast(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
        if isinstance(node.left, ast.Name) and node.left.id == "x":
            return not _has_variable(node.right)
        if isinstance(node.right, ast.Name) and node.right.id == "x":
            return not _has_variable(node.left)
        return not _has_variable(node.left) and not _has_variable(node.right)
    return False


MAX_LINEAR_SLOPE = 10


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

    if not _has_variable(tree):
        raise UnsafeExpression("константы запрещены — используйте x")

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

    if _is_linear_ast(tree):
        try:
            f0 = f(0.0)
            f1 = f(1.0)
            k = f1 - f0
            if math.isfinite(k) and abs(k) > MAX_LINEAR_SLOPE:
                raise UnsafeExpression(f"наклон |k|={abs(k):.1f} > {MAX_LINEAR_SLOPE} — слишком плоская траектория")
        except UnsafeExpression:
            raise
        except Exception:
            pass

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

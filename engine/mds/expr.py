"""mds.expr —— 受限表达式求值

工作流 YAML 里的 `expr` 字段由用户/工程师编写，可能来自不可信的物料包，
因此**绝不使用 eval/exec**：解析成 AST 后按节点白名单逐个放行，
任何属性访问、下标、推导式、lambda、未白名单函数调用一律拒绝。

同时提供 `substitute()`：把表达式里的变量名换成实际数值，
生成 "1.2 × 5.5" 这样的代入式——这是阶段 3 计算卡片"代入"栏的内容。
"""

from __future__ import annotations

import ast
import math
from typing import Any

from .errors import ExprError

# --- 白名单 ---------------------------------------------------------------

_FUNCS = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    # ent[] 是机械设计手册里的取整记号，等价于向下取整
    "ent": math.floor,
    # 三角函数（弧度制）。链轮分度圆 d = p/sin(π/z)、齿轮的重合度与变位计算都要用。
    # 它们是纯函数、无副作用、参数与返回都是数值，加进白名单不影响表达式求值的安全边界。
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "radians": math.radians,
    "degrees": math.degrees,
}

_CONSTS = {"pi": math.pi, "e": math.e}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_CMP_OPS = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _parse(src: str) -> ast.Expression:
    try:
        return ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"表达式语法错误：{src!r} —— {exc.msg}") from exc


def evaluate(src: str, env: dict[str, Any]) -> Any:
    """按白名单求值。env 里找不到的名字直接报错，不静默取 None。"""
    return _eval_node(_parse(src).body, env, src)


def _eval_node(node: ast.AST, env: dict, src: str) -> Any:
    if isinstance(node, ast.Constant):
        # 字符串常量只为枚举判等服务（分支守卫 target == 'chain'）。
        # 字符串参与运算一律在 BinOp 处拦掉，"a" * 10 这类内存炸弹进不来。
        if isinstance(node.value, (int, float, bool, str)):
            return node.value
        raise ExprError(f"表达式只允许数值或字符串常量，收到 {node.value!r}（{src}）")

    if isinstance(node, ast.Name):
        name = node.id
        if name in env:
            return env[name]
        if name in _CONSTS:
            return _CONSTS[name]
        raise ExprError(f"表达式引用了未定义的变量 {name!r}（{src}）")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExprError(f"不支持的运算符 {type(node.op).__name__}（{src}）")
        left = _eval_node(node.left, env, src)
        right = _eval_node(node.right, env, src)
        if isinstance(left, str) or isinstance(right, str):
            # 字符串只能参与判等，不能参与运算——"a" * 10000000 是内存炸弹
            raise ExprError(f"字符串不能参与算术运算（{src}）")
        if type(node.op) in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ExprError(f"表达式出现除零：{src}")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, env, src)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.Not):
            return not val
        raise ExprError(f"不支持的一元运算符（{src}）")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env, src)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ExprError(f"不支持的比较运算符（{src}）")
            right = _eval_node(comparator, env, src)
            if ((isinstance(left, str) or isinstance(right, str))
                    and type(op_node) not in (ast.Eq, ast.NotEq)):
                raise ExprError(
                    f"字符串只支持 == 与 != 比较，大小比较没有意义（{src}）")
            if not op(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, env, src) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)

    if isinstance(node, ast.IfExp):
        return (_eval_node(node.body, env, src)
                if _eval_node(node.test, env, src)
                else _eval_node(node.orelse, env, src))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExprError(f"只允许调用白名单内的具名函数（{src}）")
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise ExprError(
                f"函数 {node.func.id!r} 不在白名单内，可用：{', '.join(sorted(_FUNCS))}（{src}）")
        if node.keywords:
            raise ExprError(f"表达式中的函数调用不支持关键字参数（{src}）")
        return fn(*[_eval_node(a, env, src) for a in node.args])

    raise ExprError(f"表达式中出现被禁止的语法 {type(node).__name__}（{src}）")


# --- 代入式渲染 -----------------------------------------------------------

def fmt_num(v: Any, digits: int = 4) -> str:
    """数值格式化：去掉浮点噪声和多余的 0。"""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return str(v)
        s = f"{round(v, digits):.{digits}f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


class _Substituter(ast.NodeTransformer):
    def __init__(self, env: dict):
        self.env = env

    def visit_Name(self, node: ast.Name):  # noqa: N802
        if node.id in self.env:
            # 直接换成格式化后的字面量文本：unparse 会原样打印 Name.id
            return ast.Name(id=fmt_num(self.env[node.id]), ctx=ast.Load())
        return node


_PRETTY = [(" * ", " × "), ("**", "^")]


def substitute(src: str, env: dict[str, Any]) -> str:
    """把表达式里的变量替换成数值，返回可读的代入式。"""
    try:
        tree = _Substituter(env).visit(_parse(src))
        ast.fix_missing_locations(tree)
        text = ast.unparse(tree)
    except ExprError:
        raise
    except Exception:  # unparse 失败不应该让整次选型挂掉
        return src
    for old, new in _PRETTY:
        text = text.replace(old, new)
    return text


def referenced_names(src: str) -> set[str]:
    """表达式引用到的变量名（不含常量与函数名）。"""
    return {
        n.id for n in ast.walk(_parse(src))
        if isinstance(n, ast.Name) and n.id not in _FUNCS and n.id not in _CONSTS
    }


def simple_equality(src: str) -> tuple[str, Any] | None:
    """把 `field == 'value'` 解析成 (field, value)，其余形式返回 None。

    分支型工作流的 required_when 绝大多数就是这个形状。解析出来交给界面，
    前端就能按当前选择显隐分支字段，而不必在浏览器里再实现一套表达式求值
    ——同一套规则两处实现，迟早会对不上。
    """
    if not src or not src.strip():
        return None
    try:
        node = _parse(src).body
    except ExprError:
        return None
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    if not isinstance(node.ops[0], ast.Eq):
        return None
    if not isinstance(node.left, ast.Name):
        return None
    right = node.comparators[0]
    if not isinstance(right, ast.Constant):
        return None
    return node.left.id, right.value

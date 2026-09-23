"""
calc_engine.py —— 机械设计选型通用计算引擎

提供：
- 单位换算
- 二维表插值（用于工况系数、额定功率表）
- 校核（带上下限的指标）
- 标准值圆整（委托给 standard_round.py）

调用示例：

    python scripts/calc_engine.py unit 5.5 kW=W
    python scripts/calc_engine.py interp --table L:n1=1450,z1=24
    python scripts/calc_engine.py check --name "带速" --value 38 --limit 40 --unit "m/s"

所有函数也可被 import 直接调用，例如：

    from calc_engine import unit, interpolate, check
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Sequence


# ---------------- 单位换算 ----------------

TO_BASE = {
    # 长度 -> mm
    "mm": 1.0, "cm": 10.0, "m": 1000.0,
    "in": 25.4, "inch": 25.4, "ft": 304.8,
    # 力 -> N
    "N": 1.0, "kN": 1000.0, "kgf": 9.80665,
    # 功率 -> kW
    "kW": 1.0, "W": 1e-3, "hp": 0.73549875, "ps": 0.73549875,
    # 转速 -> r/min
    "rpm": 1.0, "r/min": 1.0, "rps": 60.0,
    # 力矩 -> N·m
    "N*m": 1.0, "N·m": 1.0, "kgf*m": 9.80665,
    "kN*m": 1000.0, "kN·m": 1000.0,
}


def unit(value: float, src: str, dst: str = "base") -> float:
    """单位换算（默认转 SI 基本单位）。"""
    src = src.strip()
    dst = dst.strip()
    if src not in TO_BASE:
        raise ValueError(f"未知源单位 {src!r}")
    base = value * TO_BASE[src]
    if dst in ("base", "si"):
        return base
    if dst not in TO_BASE:
        raise ValueError(f"未知目标单位 {dst!r}")
    return base / TO_BASE[dst]


# ---------------- 二维插值 ----------------

def interpolate(points: Sequence[tuple[float, float, float]],
                x: float, y: float) -> float:
    """双线性插值，points = [(x, y, z), ...]。

    若点处于外凸包外，则按最近邻外推。
    """
    pts = sorted(points, key=lambda p: (p[0], p[1]))
    xs = sorted({p[0] for p in pts})
    ys = sorted({p[1] for p in pts})

    if x <= xs[0]:
        x_lo = x_hi = xs[0]
    elif x >= xs[-1]:
        x_lo = x_hi = xs[-1]
    else:
        # 找包围 x 的两档
        x_hi = next(v for v in xs if v >= x)
        x_lo = max(v for v in xs if v <= x)

    if y <= ys[0]:
        y_lo = y_hi = ys[0]
    elif y >= ys[-1]:
        y_lo = y_hi = ys[-1]
    else:
        y_hi = next(v for v in ys if v >= y)
        y_lo = max(v for v in ys if v <= y)

    def z(xv, yv):
        candidates = [p for p in pts if p[0] == xv and p[1] == yv]
        if not candidates:
            # 退化为单维插值/外推
            col = [p for p in pts if p[0] == xv]
            if col:
                col.sort(key=lambda p: p[1])
                return _lerp_axis([(p[1], p[2]) for p in col], yv)
            row = [p for p in pts if p[1] == yv]
            row.sort(key=lambda p: p[0])
            return _lerp_axis([(p[0], p[2]) for p in row], xv)
        return candidates[0][2]

    if x_lo == x_hi and y_lo == y_hi:
        return z(x_lo, y_lo)

    z_ll = z(x_lo, y_lo)
    z_lh = z(x_lo, y_hi)
    z_hl = z(x_hi, y_lo)
    z_hh = z(x_hi, y_hi)

    if x_lo == x_hi:
        return _lerp_axis([(y_lo, z_ll), (y_hi, z_lh)], y)
    if y_lo == y_hi:
        return _lerp_axis([(x_lo, z_ll), (x_hi, z_hl)], x)

    tx = (x - x_lo) / (x_hi - x_lo)
    ty = (y - y_lo) / (y_hi - y_lo)
    return (z_ll * (1 - tx) * (1 - ty)
            + z_hl * tx * (1 - ty)
            + z_lh * (1 - tx) * ty
            + z_hh * tx * ty)


def _lerp_axis(axis: Iterable[tuple[float, float]], v: float) -> float:
    pts = sorted(axis, key=lambda p: p[0])
    if v <= pts[0][0]:
        return pts[0][1]
    if v >= pts[-1][0]:
        return pts[-1][1]
    lo = max(p for p in pts if p[0] <= v)
    hi = min(p for p in pts if p[0] >= v)
    if lo[0] == hi[0]:
        return lo[1]
    t = (v - lo[0]) / (hi[0] - lo[0])
    return lo[1] + t * (hi[1] - lo[1])


# ---------------- 校核 ----------------

def check(name: str, value: float, limit: float, op: str = "<=",
          unit_zh: str = "") -> dict:
    """返回校核结论 dict。"""
    fn = {"<=": lambda a, b: a <= b,
          "<":  lambda a, b: a < b,
          ">=": lambda a, b: a >= b,
          ">":  lambda a, b: a > b,
          "==": lambda a, b: math.isclose(a, b),
          "≈":  lambda a, b: math.isclose(a, b)
          }[op]
    passed = bool(fn(value, limit))
    return {
        "name": name,
        "value": round(value, 4),
        "limit": limit,
        "op": op,
        "unit": unit_zh,
        "pass": passed,
    }


# ---------------- CLI ----------------

def _cli_unit(args):
    out = unit(args.value, args.src, args.dst)
    print(json.dumps({"from": args.src, "to": args.dst,
                      "input": args.value, "output": out}))


def _cli_interp(args):
    """从 YAML 读取二维表后插值。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _yaml import load
    with open(args.file, encoding="utf-8") as f:
        data = load(f)
    pts = [tuple(p) for p in data["points"]]
    print(json.dumps({"x": args.x, "y": args.y, "z": interpolate(pts, args.x, args.y)}))


def _cli_check(args):
    print(json.dumps(check(args.name, args.value, args.limit, args.op, args.unit),
                     ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pu = sub.add_parser("unit")
    pu.add_argument("value", type=float)
    pu.add_argument("src")
    pu.add_argument("dst", default="base")
    pu.set_defaults(func=_cli_unit)

    pi = sub.add_parser("interp")
    pi.add_argument("--file", required=True)
    pi.add_argument("--x", type=float, required=True)
    pi.add_argument("--y", type=float, required=True)
    pi.set_defaults(func=_cli_interp)

    pc = sub.add_parser("check")
    pc.add_argument("--name", required=True)
    pc.add_argument("--value", type=float, required=True)
    pc.add_argument("--limit", type=float, required=True)
    pc.add_argument("--op", default="<=")
    pc.add_argument("--unit", default="")
    pc.set_defaults(func=_cli_check)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

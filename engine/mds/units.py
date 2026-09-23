"""mds.units —— 单位换算

换算表取自原 scripts/calc_engine.py，行为保持一致。
"""

from __future__ import annotations

from .errors import InputError

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


def convert(value: float, src: str, dst: str = "base") -> float:
    """单位换算（dst 省略时换算到该量纲的基准单位）。"""
    src = (src or "").strip()
    dst = (dst or "base").strip()
    if src not in TO_BASE:
        raise InputError(f"未知源单位 {src!r}")
    base = value * TO_BASE[src]
    if dst in ("base", "si"):
        return base
    if dst not in TO_BASE:
        raise InputError(f"未知目标单位 {dst!r}")
    return base / TO_BASE[dst]


# 兼容旧脚本的函数名
unit = convert

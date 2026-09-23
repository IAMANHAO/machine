"""mds.tables —— 查表：精确查找 / 分档 / 插值 / 标准系列

本模块承担引擎最重要的一条硬规则：**拒绝外推**。

原 scripts/calc_engine.py 的 interpolate() 在数据点凸包之外会静默按最近邻外推。
rated_power.yaml 每种带型只有 2~4 个示例点，拿它算任意工况会编出一个看起来
合理的 P0。这里一律改为抛 DataMissing，并报出精确缺口。
"""

from __future__ import annotations

import re
from typing import Any

from .errors import DataMissing, SpecError
from .knowledge import Table

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_path(path: str, env: dict) -> str:
    """把 'belt_types.{belt_type}.pitch_pb' 里的 {var} 用 env 填上。"""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in env or env[name] is None:
            raise SpecError(f"查表路径 {path!r} 需要变量 {name!r}，但当前上下文里没有")
        return str(env[name])
    return _PLACEHOLDER.sub(repl, path)


def walk(table: Table, path: str, env: dict | None = None) -> Any:
    """按点分路径取值；任一段缺失都抛 DataMissing 并列出该层可用键。"""
    rendered = render_path(path, env or {})
    node: Any = table.data
    walked: list[str] = []
    for part in rendered.split("."):
        if not isinstance(node, dict) or part not in node:
            available = sorted(map(str, node)) if isinstance(node, dict) else None
            here = ".".join(walked) or "(根)"
            raise DataMissing(
                f"{table.name}.yaml 中找不到 {rendered}：在 {here} 下没有 {part!r}",
                table=f"{table.material}/{table.name}", path=rendered,
                available=available,
                gap=f"需要在 {table.name}.yaml 的 {here} 下补 {part!r}",
            )
        node = node[part]
        walked.append(part)
    return node


def lookup(table: Table, path: str, env: dict | None = None) -> Any:
    """精确查表，要求结果是标量。"""
    value = walk(table, path, env)
    if isinstance(value, (dict, list)):
        raise DataMissing(
            f"{table.name}.yaml 的 {render_path(path, env or {})} 不是一个数值",
            table=f"{table.material}/{table.name}", path=path,
            available=sorted(map(str, value)) if isinstance(value, dict) else value,
        )
    return value


def series(table: Table, path: str, env: dict | None = None) -> list[float]:
    """取标准系列（list）。"""
    value = walk(table, path, env)
    if isinstance(value, dict) and "series" in value:
        value = value["series"]
    if not isinstance(value, list) or not value:
        raise DataMissing(
            f"{table.name}.yaml 的 {render_path(path, env or {})} 不是一个非空系列",
            table=f"{table.material}/{table.name}", path=path,
        )
    return [float(v) for v in value]


_ROW_CMP = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def rows(table: Table, path: str, env: dict | None = None) -> list[dict]:
    """取一组行（list of dict）。用于"在有序行集里挑一行"的选型步骤。"""
    value = walk(table, path, env)
    if isinstance(value, dict) and "rows" in value:
        value = value["rows"]
    if not isinstance(value, list) or not value:
        raise DataMissing(
            f"{table.name}.yaml 的 {render_path(path, env or {})} 不是一个非空行集",
            table=f"{table.material}/{table.name}", path=path,
        )
    bad = [r for r in value if not isinstance(r, dict)]
    if bad:
        raise SpecError(
            f"{table.name}.yaml 的 {render_path(path, env or {})} 里有非映射的行："
            f"{bad[0]!r}")
    return value


def rows_where(candidates: list[dict], conditions: list[dict], *,
               order_by: str | None = None, table: Table | None = None,
               path: str = "") -> list[dict]:
    """筛出全部满足条件的行，按 order_by 升序。

    条件写作 {field, op, value}，value 已由调用方解析成数值。
    行里缺 field 的，**当作不满足**而不是跳过——一张缺格子的表不该
    让某一行悄悄胜出。缺的格子由审计去报。
    """
    where = f"{table.material}/{table.name}" if table else None

    def ok(row: dict) -> bool:
        for c in conditions:
            field, op = c["field"], c.get("op", ">=")
            if op not in _ROW_CMP:
                raise SpecError(f"行筛选的比较符 {op!r} 非法，可用：{', '.join(_ROW_CMP)}")
            if field not in row or row[field] is None:
                return False
            try:
                left = float(row[field])
            except (TypeError, ValueError):
                left = row[field]
            if not _ROW_CMP[op](left, c["value"]):
                return False
        return True

    hit = [r for r in candidates if ok(r)]
    if order_by:
        missing = [r for r in hit if order_by not in r]
        if missing:
            raise DataMissing(
                f"{table.name if table else '行集'} 里有行缺排序字段 {order_by!r}",
                table=where, path=path,
                gap=f"需要给每一行补 {order_by}",
            )
        hit.sort(key=lambda r: float(r[order_by]))
    return hit


def bucket(value: float, bins: list[dict]) -> str:
    """把连续量落到命名分档（如 n1=1450 -> 'n1_900_to_1800'）。

    bins: [{max: 900, key: n1_le_900}, ..., {key: n1_gt_3600}]
    最后一档可以省略 max，表示兜底。
    """
    for b in bins:
        if "key" not in b:
            raise SpecError(f"分档定义缺少 key：{b!r}")
        if "max" not in b or value <= float(b["max"]):
            return str(b["key"])
    raise DataMissing(
        f"数值 {value} 未落入任何分档",
        available=[b.get("key") for b in bins],
        gap="分档定义需要一个无 max 的兜底档",
    )


# --- 插值（拒绝外推） ------------------------------------------------------

def _lerp(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _bracket(values: list[float], x: float, *, what: str, table: Table,
             coords: dict) -> tuple[float, float]:
    """找到夹住 x 的两个刻度；x 在范围外直接抛 DataMissing，不外推。"""
    lo, hi = min(values), max(values)
    if x < lo or x > hi:
        raise DataMissing(
            f"{table.name}.yaml 覆盖的 {what} 范围是 [{_fmt(lo)}, {_fmt(hi)}]，"
            f"当前工况 {what}={_fmt(x)} 超出范围，无法计算（引擎不做外推）",
            table=f"{table.material}/{table.name}", coords=coords,
            available=sorted(values),
            gap=f"需要在 {table.name}.yaml 中补 {what}={_fmt(x)} 附近的数据点",
        )
    below = [v for v in values if v <= x]
    above = [v for v in values if v >= x]
    return max(below), min(above)


def _fmt(v: float) -> str:
    return f"{v:g}"


def interp2d(table: Table, group_path: str, rows_key: str,
             x_field: str, y_field: str, z_field: str,
             x: float, y: float, env: dict | None = None) -> float:
    """在 group 下的 rows 结构上做二维插值。

    结构形如：
        belt_type_L:
          examples:
            - n1: 1450
              points:
                - {z1: 18, P0: 0.65}

    x 走 rows 的 x_field，y 走每行 points 里的 y_field，z 取 z_field。
    x 或 y 超出覆盖范围一律 DataMissing。
    """
    group = walk(table, group_path, env)
    rows = group.get(rows_key) if isinstance(group, dict) else None
    if not isinstance(rows, list) or not rows:
        raise DataMissing(
            f"{table.name}.yaml 的 {render_path(group_path, env or {})}.{rows_key} 没有数据行",
            table=f"{table.material}/{table.name}", path=group_path,
            gap=f"需要补 {render_path(group_path, env or {})}.{rows_key}",
        )

    coords = {x_field: x, y_field: y}
    xs = sorted({float(r[x_field]) for r in rows if x_field in r})
    if not xs:
        raise DataMissing(f"{table.name}.yaml 的数据行里没有 {x_field} 字段",
                          table=f"{table.material}/{table.name}", coords=coords)
    x_lo, x_hi = _bracket(xs, x, what=x_field, table=table, coords=coords)

    def row_value(xv: float) -> float:
        row = next(r for r in rows if float(r[x_field]) == xv)
        pts = row.get("points") or []
        pairs = sorted((float(p[y_field]), float(p[z_field]))
                       for p in pts if y_field in p and z_field in p)
        if not pairs:
            raise DataMissing(
                f"{table.name}.yaml 中 {x_field}={_fmt(xv)} 这一行没有可用数据点",
                table=f"{table.material}/{table.name}", coords=coords,
                gap=f"需要补 {x_field}={_fmt(xv)} 行的 {y_field}/{z_field} 数据点",
            )
        ys = [p[0] for p in pairs]
        y_lo, y_hi = _bracket(
            ys, y, what=f"{y_field}（{x_field}={_fmt(xv)} 时）", table=table, coords=coords)
        z_lo = next(z for yy, z in pairs if yy == y_lo)
        z_hi = next(z for yy, z in pairs if yy == y_hi)
        return _lerp(y_lo, z_lo, y_hi, z_hi, y)

    if x_lo == x_hi:
        return row_value(x_lo)
    return _lerp(x_lo, row_value(x_lo), x_hi, row_value(x_hi), x)

"""mds.rounding —— 圆整到标准系列

相对原 scripts/standard_round.py 的关键改动：当需求值超出系列上限时，
不再静默返回系列最大值。`on_exceed="error"` 会抛 DataMissing —— 因为
"所需带宽 194mm，而 L 型最大标准带宽 101.6mm"是一个选型结论（该换带型），
不是一个可以四舍五入掉的细节。
"""

from __future__ import annotations

from .errors import DataMissing, NoSolution, SpecError

MODES = ("nearest_above", "nearest_below", "nearest")


def round_to_series(value: float, series: list[float], mode: str = "nearest_above",
                    *, on_exceed: str = "clamp", what: str = "数值",
                    table: str | None = None) -> dict:
    """把 value 圆整到 series。返回含圆整详情的 dict，便于写进 trace。"""
    if mode not in MODES:
        raise SpecError(f"未知圆整模式 {mode!r}，可用：{', '.join(MODES)}")
    if not series:
        raise DataMissing(f"{what} 的标准系列为空", table=table)

    ordered = sorted(series)
    lo, hi = ordered[0], ordered[-1]
    exceeded = None

    if mode == "nearest_above":
        candidates = [s for s in ordered if s >= value]
        if candidates:
            chosen = min(candidates)
        else:
            exceeded = "above"
            chosen = hi
    elif mode == "nearest_below":
        candidates = [s for s in ordered if s <= value]
        if candidates:
            chosen = max(candidates)
        else:
            exceeded = "below"
            chosen = lo
    else:
        chosen = min(ordered, key=lambda s: abs(s - value))

    if exceeded and on_exceed == "error":
        bound, word = (hi, "上限") if exceeded == "above" else (lo, "下限")
        bigger = "更大规格 / 更大节距的型号" if exceeded == "above" else "更小规格的型号"
        raise NoSolution(
            f"所需{what} {value:g} 超出标准系列{word} {bound:g}，本规格系列内无解",
            table=table, what=what, required=round(value, 4),
            limit=bound, available=ordered,
            remedy=f"改用{bigger}，或降低传递功率 / 调整传动比",
        )

    return {
        "value": chosen,
        "input": value,
        "mode": mode,
        "index": ordered.index(chosen),
        "series_range": [lo, hi],
        "series_size": len(ordered),
        "exceeded": exceeded,
    }

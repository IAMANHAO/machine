"""
standard_round.py —— 将任意数值圆整到 YAML 中的标准系列

使用方式：

    python scripts/standard_round.py 612 belt_length_series --belt-type L --out equal

返回一个 dict，便于 SKILL 引擎直接使用。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _yaml import load, dump, HAS_PYYAML  # noqa: E402


SKILL_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = SKILL_ROOT / "knowledge" / "cache"


def _load_series(material: str, yaml_name: str) -> list[float]:
    """从 knowledge/cache/<material>/<yaml_name>.yaml 中读取 'series' 字段。"""
    path = CACHE_ROOT / material / f"{yaml_name}.yaml"
    if not path.exists():
        sys.exit(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        data = load(f)

    # 单 series
    if "series" in data:
        return list(data["series"])

    # 按 belt-type 分组：belt_width.yaml / belt_length.yaml
    if material == "synchronous_belt":
        for key in data.keys():
            v = data[key]
            if isinstance(v, dict) and "series" in v:
                return list(v["series"])
            if isinstance(v, dict) and key.startswith("belt_"):
                # 这里没匹配到具体带型，提示
                continue

    sys.exit(f"该 yaml 中未找到 series 字段；请检查 {path}")


def _load_grouped_series(material: str, yaml_name: str, group_key: str) -> list[float]:
    """读取 knowledge/cache/<material>/<yaml_name>.yaml，按 group_key（带型）取 series。

    支持 group_key 嵌套在某个中间 dict 下（如 belt_length_series.yaml 的 belt_length 下）。
    """
    path = CACHE_ROOT / material / f"{yaml_name}.yaml"
    if not path.exists():
        sys.exit(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        data = load(f)
    series = _find_series(data, group_key)
    if series is None:
        sys.exit(f"{yaml_name}.yaml 中找不到 {group_key}.series")
    return list(series)


def _find_series(node, group_key):
    """递归在 dict 树里查找 group_key，并返回其 series。"""
    if isinstance(node, dict):
        if group_key in node and isinstance(node[group_key], dict) \
                and "series" in node[group_key]:
            return node[group_key]["series"]
        if group_key in node and isinstance(node[group_key], list):
            return node[group_key]
        for v in node.values():
            found = _find_series(v, group_key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_series(item, group_key)
            if found is not None:
                return found
    return None


def round_to_standard(value: float, series: list[float],
                      mode: str = "nearest_above") -> tuple[float, int]:
    """把 value 圆整到 series 中最接近的项。

    mode:
      - 'nearest_above'：≥ value 的最小项（推荐用于节线长）
      - 'nearest_below'：≤ value 的最大项
      - 'nearest'      ：距离最近的项

    返回 (圆整值, 在 series 中的索引)
    """
    if not series:
        raise ValueError("标准系列不能为空")
    series_sorted = sorted(series)
    if mode == "nearest_above":
        candidates = [s for s in series_sorted if s >= value]
        if not candidates:
            return series_sorted[-1], len(series_sorted) - 1
        chosen = min(candidates)
    elif mode == "nearest_below":
        candidates = [s for s in series_sorted if s <= value]
        if not candidates:
            return series_sorted[0], 0
        chosen = max(candidates)
    elif mode == "nearest":
        chosen = min(series_sorted, key=lambda s: abs(s - value))
    else:
        raise ValueError(f"未知 mode {mode!r}")

    idx = series_sorted.index(chosen)
    return chosen, idx


def _cli(args):
    series = _load_grouped_series(args.material, args.file, args.belt_type)
    value, idx = round_to_standard(args.value, series, args.mode)
    print(json.dumps({
        "input": args.value,
        "result": value,
        "index": idx,
        "mode": args.mode,
        "belt_type": args.belt_type,
        "series": series,
    }, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("value", type=float, help="需要圆整的数值")
    p.add_argument("file", help="YAML 文件名（不含扩展名），位于 knowledge/cache/<material>/")
    p.add_argument("--material", default="synchronous_belt")
    p.add_argument("--belt-type", required=True,
                   help="同一文件中按带型分组的 key，如 L / H / XL")
    p.add_argument("--mode", choices=["nearest_above", "nearest_below", "nearest"],
                   default="nearest_above")
    args = p.parse_args()
    _cli(args)


if __name__ == "__main__":
    main()

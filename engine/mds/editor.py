"""mds.editor —— 缓存数据表的安全写入

缓存文件里有大量有价值的注释（公式、用法、录入规则），PyYAML 落盘会把它们
全部抹掉。写入路径因此改用 ruamel.yaml 的 round-trip 模式保留注释与格式。

两条写入规则：

1. **标 verified 必须有第二信源。** 没有 second_source 一律拒绝——
   假绿灯比没有绿灯更危险。
2. **只改指定路径。** 不做整文件重写，改哪个点写哪个点。
"""

from __future__ import annotations

import datetime as _dt
import io
import shutil
from pathlib import Path
from typing import Any

from .errors import DataMissing, MDSError
from .knowledge import META_KEYS, SKILL_ROOT

try:
    from ruamel.yaml import YAML
    HAS_RUAMEL = True
except ImportError:  # pragma: no cover - 只有写入路径需要它
    HAS_RUAMEL = False


class EditError(MDSError):
    """写入被拒绝（校验不过、路径不存在、缺依赖等）。"""


def _yaml() -> "YAML":
    if not HAS_RUAMEL:
        raise EditError(
            "编辑缓存数据需要 ruamel.yaml（用于保留文件里的注释）。"
            "请安装：pip install ruamel.yaml")
    y = YAML()
    y.preserve_quotes = True
    # 保留开头的 ---，与其余缓存文件的两段式写法一致
    y.explicit_start = True
    y.width = 4096          # 不要自动折行，中文表格折了很难读
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def table_path(material: str, name: str, root: Path | None = None) -> Path:
    name = name[:-5] if name.endswith(".yaml") else name
    path = (root or SKILL_ROOT) / "knowledge" / "cache" / material / f"{name}.yaml"
    if not path.exists():
        raise DataMissing(f"没有数据表 {material}/{name}.yaml", table=f"{material}/{name}")
    return path


def _load_docs(path: Path) -> list:
    y = _yaml()
    return list(y.load_all(path.read_text(encoding="utf-8")))


def _dump_docs(path: Path, docs: list) -> None:
    y = _yaml()
    buf = io.StringIO()
    y.dump_all(docs, buf)
    text = buf.getvalue()
    # 备份一份，改错了能找回来
    shutil.copy2(path, path.with_suffix(".yaml.bak"))
    path.write_text(text, encoding="utf-8")


def _split(docs: list) -> tuple[Any, Any]:
    """返回 (frontmatter 文档, 正文文档)。单文档文件两者相同。"""
    if len(docs) >= 2:
        return docs[0], docs[1]
    return docs[0], docs[0]


def _today() -> str:
    return _dt.date.today().isoformat()


# --- 信源与核验状态 --------------------------------------------------------

def set_sources(material: str, name: str, *, data_source: str | None = None,
                second_source: str | None = None, note: str | None = None,
                root: Path | None = None) -> dict:
    """更新信源字段（不改核验状态）。"""
    path = table_path(material, name, root)
    docs = _load_docs(path)
    front, _ = _split(docs)
    if data_source is not None:
        front["data_source"] = data_source.strip()
    if second_source is not None:
        front["second_source"] = second_source.strip()
    if note is not None:
        front["_todo"] = note.strip()
    _dump_docs(path, docs)
    return {"file": path.name, "data_source": front.get("data_source", ""),
            "second_source": front.get("second_source", "")}


def mark_verified(material: str, name: str, *, second_source: str,
                  verified_by: str = "", root: Path | None = None) -> dict:
    """标记为已双源核验。

    这是 M3 收紧的地方：必须给出第二信源，否则拒绝。
    对齐 references/source_priority.md 的"关键工况系数需 ≥ 第 2 级 + 至少 2 个信源"。
    """
    second = (second_source or "").strip()
    if not second:
        raise EditError(
            "标记 verified 必须给出第二信源。"
            "单一信源的数据请保持 single_source —— 假绿灯比没有绿灯更危险。")

    path = table_path(material, name, root)
    docs = _load_docs(path)
    front, _ = _split(docs)

    primary = str(front.get("data_source") or "").strip()
    if not primary:
        raise EditError(f"{name}.yaml 还没有 data_source，无法核验")
    if second == primary:
        raise EditError("第二信源与主信源相同，这不构成交叉验证")

    front["second_source"] = second
    front["verification_status"] = "verified"
    front["last_verified"] = _today()
    if verified_by:
        front["verified_by"] = verified_by.strip()
    front.pop("_todo", None)
    _dump_docs(path, docs)
    return {"file": path.name, "verification_status": "verified",
            "data_source": primary, "second_source": second,
            "last_verified": front["last_verified"]}


def unverify(material: str, name: str, reason: str = "", root: Path | None = None) -> dict:
    """退回 single_source（发现数据有问题时用）。"""
    path = table_path(material, name, root)
    docs = _load_docs(path)
    front, _ = _split(docs)
    front["verification_status"] = "single_source"
    front["last_verified"] = _today()
    if reason:
        front["_todo"] = reason.strip()
    _dump_docs(path, docs)
    return {"file": path.name, "verification_status": "single_source"}


# --- 数据点录入 ------------------------------------------------------------

def _walk_mut(node: Any, parts: list[str], path_text: str) -> Any:
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise DataMissing(
                f"路径 {path_text} 在 {part!r} 处走不下去",
                path=path_text,
                available=sorted(map(str, node)) if isinstance(node, dict) else None)
        node = node[part]
    return node


def upsert_points(material: str, name: str, *, group: str, rows_key: str,
                  x_field: str, x_value: float, y_field: str, z_field: str,
                  points: list[dict], root: Path | None = None) -> dict:
    """往插值表的某一行里补/改数据点。

    points: [{<y_field>: 18, <z_field>: 1.6}, ...]
    行不存在就新建；行内同 y 值的点会被覆盖。
    """
    if not points:
        raise EditError("没有要写入的数据点")
    for p in points:
        if y_field not in p or z_field not in p:
            raise EditError(f"数据点必须同时给出 {y_field} 与 {z_field}：{p!r}")
        try:
            float(p[y_field]); float(p[z_field])
        except (TypeError, ValueError):
            raise EditError(f"数据点必须是数值：{p!r}") from None

    path = table_path(material, name, root)
    docs = _load_docs(path)
    front, body = _split(docs)

    grp = _walk_mut(body, group.split("."), group)
    if not isinstance(grp, dict):
        raise EditError(f"{group} 不是一个分组")
    rows = grp.setdefault(rows_key, [])

    row = next((r for r in rows if float(r.get(x_field, float("nan"))) == float(x_value)), None)
    if row is None:
        row = {x_field: x_value, "points": []}
        rows.append(row)
        rows.sort(key=lambda r: float(r.get(x_field, 0)))

    existing = row.setdefault("points", [])
    written = 0
    for p in points:
        yv, zv = float(p[y_field]), float(p[z_field])
        hit = next((e for e in existing if float(e.get(y_field, float("nan"))) == yv), None)
        if hit is not None:
            hit[z_field] = zv
        else:
            existing.append({y_field: yv, z_field: zv})
        written += 1
    existing.sort(key=lambda e: float(e.get(y_field, 0)))

    # 数据变了，核验状态必须退回去重新核
    if front.get("verification_status") == "verified":
        front["verification_status"] = "single_source"
        front["_todo"] = "数据在核验后被修改，需要重新双源核对"
    front["last_verified"] = _today()

    _dump_docs(path, docs)
    return {"file": path.name, "group": group, x_field: x_value, "written": written,
            "row_size": len(existing)}


def set_scalar(material: str, name: str, *, path: str, value: Any,
               root: Path | None = None) -> dict:
    """改一个标量（如 belt_types.H.vmax）。"""
    file_path = table_path(material, name, root)
    docs = _load_docs(file_path)
    front, body = _split(docs)

    parts = path.split(".")
    parent = _walk_mut(body, parts[:-1], path) if len(parts) > 1 else body
    leaf = parts[-1]
    if not isinstance(parent, dict) or leaf not in parent:
        raise DataMissing(f"{name}.yaml 里没有路径 {path}", path=path,
                          available=sorted(map(str, parent)) if isinstance(parent, dict) else None)
    if isinstance(parent[leaf], (dict, list)):
        raise EditError(f"{path} 不是标量，不能用这个接口改")

    old = parent[leaf]
    parent[leaf] = value
    if front.get("verification_status") == "verified":
        front["verification_status"] = "single_source"
        front["_todo"] = "数据在核验后被修改，需要重新双源核对"
    front["last_verified"] = _today()

    _dump_docs(file_path, docs)
    return {"file": file_path.name, "path": path, "old": old, "new": value}


__all__ = [
    "EditError", "HAS_RUAMEL", "table_path",
    "set_sources", "mark_verified", "unverify", "upsert_points", "set_scalar",
]

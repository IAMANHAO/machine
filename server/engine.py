"""引擎适配层 —— 把 mds 的对象翻译成 API 形状。

这一层只做形状转换与缓存，不含任何计算逻辑：所有数值一律来自 mds。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import ensure_engine_importable

ensure_engine_importable()

import mds  # noqa: E402
from mds import Knowledge, load_spec  # noqa: E402
from mds import procure as mds_procure  # noqa: E402
from mds import spec as mds_spec  # noqa: E402
from mds.errors import DataMissing, InputError, MDSError, SpecError  # noqa: E402
from mds.audit import audit_material as _audit_material  # noqa: E402
from mds.runner import enum_choices, run as run_selection  # noqa: E402

__all__ = [
    "DataMissing", "InputError", "MDSError", "SpecError",
    "knowledge", "materials", "workflow_payload", "execute", "procure_build",
    "procure_for_trace", "audit_material", "audit_all", "read_table",
    "workflow_spec",
    "stale_sources", "engine_version", "reset_caches",
]


def engine_version() -> str:
    return mds.__version__


@lru_cache(maxsize=1)
def knowledge() -> Knowledge:
    return Knowledge()


@lru_cache(maxsize=16)
def _spec(material: str):
    return load_spec(material)


def reset_caches() -> None:
    """知识库页改了数据之后调用（M3）。"""
    knowledge.cache_clear()
    _spec.cache_clear()


def _index() -> dict:
    return knowledge().index() or {}


def _table_confidence(material: str) -> str:
    from mds.knowledge import lowest_confidence
    know = knowledge()
    names = know.tables_of(material)
    if not names:
        return "unknown"
    return lowest_confidence([know.table(material, n).confidence for n in names])


def materials() -> list[dict]:
    """首页物料入口：已就绪 / 仅有缓存 / 待生成。"""
    know = knowledge()
    index = _index()
    icons = index.get("material_icons") or {}
    meta = index.get("materials") or {}
    executable = set(mds_spec.available())
    cached = set(know.materials())

    out: list[dict] = []
    for mid in sorted(executable | cached):
        info = meta.get(mid) or {}
        ready = mid in executable
        name = info.get("name_zh") or mid
        standard = info.get("standard", "")
        doc = info.get("workflow", "")
        if ready:
            try:
                spec = _spec(mid)
                name = spec.name_zh or name
                standard = spec.standard or standard
                doc = spec.workflow_doc or doc
            except SpecError:
                ready = False
        out.append({
            "id": mid,
            "name_zh": name,
            "icon": icons.get(mid, "▦"),
            "standard": standard,
            "status": "ready" if ready else "cache_only",
            "table_count": len(know.tables_of(mid)),
            "confidence": _table_confidence(mid),
            "workflow_doc": doc,
            "note": "" if ready else "已有 .md 工作流与缓存，尚未规格化为可执行 YAML",
        })

    for planned in index.get("planned_materials") or []:
        out.append({
            "id": planned.get("id", "?"),
            "name_zh": planned.get("name_zh", planned.get("id", "?")),
            "icon": planned.get("icon", "＋"),
            "standard": planned.get("standard_hint", ""),
            "status": "planned",
            "table_count": 0,
            "confidence": "unknown",
            "workflow_doc": "",
            "note": "尚未建立工作流，需先检索依据并生成规格",
        })
    return out


def _depends_on(condition: str) -> dict | None:
    """把简单的分支条件解析给前端用于显隐。复杂条件返回 None，界面就全显示。"""
    from mds.expr import simple_equality

    parsed = simple_equality(condition)
    if parsed is None:
        return None
    field, value = parsed
    return {"field": field, "equals": value}


def workflow_spec(material: str):
    """AI 层需要规格对象本身（输入定义、枚举候选、typical 值）。"""
    return _spec(material)


def workflow_payload(material: str) -> dict:
    """阶段 2 表单所需的全部信息。"""
    spec = _spec(material)
    know = knowledge()
    options = enum_choices(spec, know)

    inputs = [{
        "id": i.id,
        "name_zh": i.name_zh or i.id,
        "unit": i.unit,
        "type": i.type,
        "required": i.required,
        "required_when": i.required_when,
        "depends_on": _depends_on(i.required_when),
        "one_of": i.one_of,
        "hint": i.hint,
        "default": i.default,
        "domain": i.domain or {},
        "options": options.get(i.id, []),
    } for i in spec.inputs]

    steps = [{"id": s.id, "name_zh": s.name_zh or s.id, "kind": s.kind,
              "unit": s.get("unit", "")} for s in spec.steps]

    sources = [know.table(material, n).source_info() for n in know.tables_of(material)]

    return {
        "material": spec.material,
        "name_zh": spec.name_zh or spec.material,
        "standard": spec.standard,
        "workflow_doc": spec.workflow_doc,
        "notes": spec.notes,
        "inputs": inputs,
        "steps": steps,
        "sources": sources,
        "confidence": _table_confidence(material),
    }


def execute(material: str, values: dict, choices: dict | None = None) -> dict:
    """跑一次选型，并在成功时附上采购链接。"""
    spec = _spec(material)
    trace = run_selection(spec, values, knowledge(), choices or {})
    payload: dict[str, Any] = {"trace": trace.to_dict(), "procure": None,
                               "procure_error": None}
    if trace.status == "ok" and spec.procure:
        try:
            payload["procure"] = mds_procure.from_spec(spec, trace.outputs)
        except MDSError as exc:
            payload["procure_error"] = str(exc)
    return payload


def procure_for_trace(material: str, trace: dict) -> dict | None:
    """从存档的 trace 重算采购链接。

    重算而不是把链接存进库：关键词模板可能更新，重算保证拿到的是当前规则下的结果。
    """
    if trace.get("status") != "ok":
        return None
    try:
        spec = _spec(material)
    except SpecError:
        return None
    if not spec.procure:
        return None
    try:
        return mds_procure.from_spec(spec, trace.get("outputs") or {})
    except MDSError:
        return None


def procure_build(material_template: str, fields: dict,
                  channels: list[str] | None = None, extra: str = "") -> dict:
    return mds_procure.build(material_template, fields, channels, extra)


def summarize(material: str, values: dict, trace: dict) -> tuple[str, str]:
    """给项目卡片用的一行工况 + 一行结论。"""
    spec = _spec(material)
    parts = []
    for i in spec.inputs:
        v = values.get(i.id)
        if v in (None, "") or i.type == "enum":
            continue
        parts.append(f"{v}{i.unit}" if i.unit else f"{i.name_zh or i.id} {v}")
    summary = " · ".join(parts[:4])

    if trace.get("status") == "ok" and trace.get("result"):
        keep = [r for r in trace["result"] if "采购" not in r["label"]][:3]
        headline = " · ".join(f"{r['value']}{r['unit']}".strip() for r in keep)
    elif trace.get("blocker"):
        headline = str(trace["blocker"].get("message", ""))[:60]
    else:
        headline = ""
    return summary, headline


# --- 知识库（M3） ----------------------------------------------------------

def audit_material(material: str, probe: bool = True) -> dict:
    """单个物料的数据体检报告。"""
    return _audit_material(material, knowledge(), probe=probe)


def audit_all(probe: bool = True) -> dict:
    """所有有缓存的物料的汇总。探测较慢，总览页默认可关掉。"""
    know = knowledge()
    reports = [_audit_material(m, know, probe=probe) for m in know.materials()]
    totals = {"tables": 0, "verified": 0, "can_be_verified": 0,
              "blocking": 0, "warning": 0, "reachable": 0, "probed": 0}
    for r in reports:
        for k in ("tables", "verified", "can_be_verified", "blocking", "warning"):
            totals[k] += r["counts"].get(k, 0)
        totals["reachable"] += r["probe"]["reachable"]
        totals["probed"] += r["probe"]["total"]
    return {"materials": reports, "totals": totals}


def read_table(material: str, name: str) -> dict:
    """一张表的完整内容 + 元数据，供知识库页展示与编辑。"""
    tbl = knowledge().table(material, name)
    return {
        "material": material,
        "name": tbl.name,
        "file": f"{tbl.name}.yaml",
        "meta": {k: (str(v) if hasattr(v, "isoformat") else v)
                 for k, v in tbl.meta.items()},
        "data": tbl.data,
        "confidence": tbl.confidence,
        "fingerprint": tbl.fingerprint,
        "path": str(tbl.path),
    }


def stale_sources(trace: dict) -> list[dict]:
    """存档 trace 引用的数据表里，哪些在那之后被改过。

    项目存的是当时的 trace；数据表改了之后那份结果就不再代表当前知识库，
    界面需要据此提示"建议重算"。
    """
    know = knowledge()
    out = []
    for src in trace.get("sources") or []:
        old = src.get("fingerprint")
        if not old:
            continue
        name = str(src.get("table_file", "")).removesuffix(".yaml")
        try:
            now = know.table(trace.get("material", ""), name).fingerprint
        except MDSError:
            out.append({"table_file": src.get("table_file"), "reason": "数据表已不存在"})
            continue
        if now != old:
            out.append({"table_file": src.get("table_file"), "reason": "数据表已更新",
                        "was": old, "now": now})
    return out

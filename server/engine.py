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
    "workflow_spec", "material_ids", "payload_of", "execute_spec",
    "stale_sources", "engine_version", "reset_caches", "note_ai_origins",
]


def engine_version() -> str:
    return mds.__version__


def material_ids() -> list[str]:
    """全部可执行物料的 id（含用户目录里保存的）。起草新物料时用来避免重名。"""
    return mds_spec.available()


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
        provenance = "builtin"
        if ready:
            try:
                spec = _spec(mid)
                name = spec.name_zh or name
                standard = spec.standard or standard
                doc = spec.workflow_doc or doc
                provenance = spec.provenance
            except SpecError:
                ready = False

        # AI 起草的物料一律按**最低置信度**显示，与"数据表缺信源"同级。
        # 它确实没有任何信源——这不是保守，是事实。
        confidence = "unknown" if provenance == "ai_generated" else _table_confidence(mid)
        note = "" if ready else "已有 .md 工作流与缓存，尚未规格化为可执行 YAML"
        if provenance == "ai_generated":
            note = "AI 起草，未经核验：公式与系数都需要你对照手册确认"

        out.append({
            "id": mid,
            "name_zh": name,
            "icon": icons.get(mid, "✦" if provenance == "ai_generated" else "▦"),
            "standard": standard,
            "status": "ready" if ready else "cache_only",
            "provenance": provenance,
            "table_count": len(know.tables_of(mid)),
            "confidence": confidence,
            "workflow_doc": doc,
            "note": note,
        })

    for planned in index.get("planned_materials") or []:
        out.append({
            "id": planned.get("id", "?"),
            "name_zh": planned.get("name_zh", planned.get("id", "?")),
            "icon": planned.get("icon", "＋"),
            "standard": planned.get("standard_hint", ""),
            "status": "planned",
            "provenance": "builtin",
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
    return payload_of(_spec(material))


def payload_of(spec) -> dict:
    """同上，但接受规格对象本身。

    引导式选型要在**还没落盘**的时候就把表单画出来——"跑通了才存"这条原则
    不能为了少写一个函数而破掉（一份没跑通的流程存下来，只会在物料列表里
    留一个点进去就报错的入口）。
    """
    material = spec.material
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
        "provenance": spec.provenance,
        "generated_by": spec.generated_by,
        "notes": spec.notes,
        "inputs": inputs,
        "steps": steps,
        "sources": sources,
        # AI 起草的流程没有任何信源表，置信度按最低档——与"数据表缺信源"同级
        "confidence": ("unknown" if spec.provenance == "ai_generated"
                       else _table_confidence(material)),
    }


def execute(material: str, values: dict, choices: dict | None = None) -> dict:
    """跑一次选型，并在成功时附上采购链接。"""
    return execute_spec(_spec(material), values, choices)


def execute_spec(spec, values: dict, choices: dict | None = None) -> dict:
    """同上，但接受规格对象本身（引导式选型在落盘前也要能跑）。

    **走的是同一个 `runner.run`**：引导式没有给引擎加任何新的执行路径，
    所以阶段 3~6 的行为与随包物料逐字相同。
    """
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


def note_ai_origins(trace: dict, ai_filled: dict, ai_aligned: dict) -> dict:
    """把"这几个值是 AI 给的"如实写进结果警告。

    **引擎不知道也不该知道这件事**——它只管算。出身是服务层的记账：
    谁填的、为什么这么填，由界面与接口层一路带过来，在这里落到 trace 上，
    再由导出报告原样带走。

    一个标着"AI 按常用值补的，理由：…"的数不是来路不明的数；
    一个混在用户手输里、看不出区别的数才是。这个函数就是那条分界线。
    """
    lines = []
    for pid, why in (ai_filled or {}).items():
        lines.append(f"{pid}（{why}）" if why else pid)
    if lines:
        trace.setdefault("warnings", []).insert(0, (
            f"⚠ 本次有 {len(lines)} 个参数不是你填的，是 AI 按常用值补的："
            + "；".join(lines) + "。"
            "它们已经过与手输相同的校验，但**校验只管取值合法，不管它适不适合你的工况**。"
            "定稿前请逐项确认。"))
    if ai_aligned:
        pairs = "；".join(f"{k} → {v}" for k, v in ai_aligned.items())
        trace.setdefault("warnings", []).insert(0, (
            f"⚠ 本次有 {len(ai_aligned)} 处取值由 AI 对齐了叫法：{pairs}。"
            "对齐只在给定候选里选，不会造出新选项；但对错了你未必看得出来，请核对。"))
    return trace


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

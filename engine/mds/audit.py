"""mds.audit —— 数据自检：信源完整性、表格覆盖度、工况可达性

三层检查，从便宜到昂贵：

1. **frontmatter 体检**：每张表是否有 data_source / verification_status / _todo；
   标了 verified 的是否真有第二信源（没有就是假绿灯，必须揪出来）。
2. **静态覆盖分析**：插值表的 (x, y) 网格里哪些格子是空的。
3. **动态可达性探测**：按工作流 probe 段的网格真跑引擎，报出哪些工况组合
   现在根本算不出来。这一层最有用——它回答的是"这个软件现在到底能选什么"，
   而不是"数据缺了几格"。

探测用的是真引擎，不是模拟：确定性、够快，且不会与实际行为脱节。
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any

from .errors import DataMissing, InputError, MDSError, NeedsChoice, SpecError
from .knowledge import CONFIDENCE_ORDER, Knowledge, Table
from .spec import WorkflowSpec

BLOCKING = "blocking"
WARNING = "warning"
INFO = "info"

# 标成 verified 必须有第二信源。缺了就是假绿灯。
REQUIRED_META = ("data_source", "verification_status", "last_verified")


@dataclass
class Gap:
    kind: str           # frontmatter | fake_green | grid_hole | empty_group | unreachable
    severity: str
    table: str          # "<material>/<file>.yaml"，frontmatter 类可为空
    message: str
    coords: dict = field(default_factory=dict)
    fix_hint: str = ""
    # 补录界面直接可用的定位信息：表名、分组、行坐标、缺哪些点
    target: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TableAudit:
    material: str
    name: str
    file: str
    confidence: str
    data_source: str
    second_source: str
    last_verified: Any
    todo: str
    can_be_verified: bool       # 是否满足标 🟢 的条件（双源）
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _second_source(tbl: Table) -> str:
    raw = tbl.meta.get("second_source") or ""
    return str(raw).strip()


def audit_table(tbl: Table) -> TableAudit:
    issues: list[str] = []
    for key in REQUIRED_META:
        if not tbl.meta.get(key):
            issues.append(f"frontmatter 缺 {key}")

    second = _second_source(tbl)
    status = tbl.confidence
    if status == "verified" and not second:
        issues.append("标为 verified 但没有 second_source —— 双源核验无从追溯")
    if status not in CONFIDENCE_ORDER and status != "self_defined":
        issues.append(f"verification_status 取值 {status!r} 不在允许集合内")

    return TableAudit(
        material=tbl.material,
        name=tbl.name,
        file=f"{tbl.name}.yaml",
        confidence=status,
        data_source=tbl.data_source,
        second_source=second,
        last_verified=tbl.meta.get("last_verified"),
        todo=str(tbl.meta.get("_todo") or ""),
        can_be_verified=bool(tbl.data_source and second),
        issues=issues,
    )


def frontmatter_gaps(audits: list[TableAudit]) -> list[Gap]:
    gaps: list[Gap] = []
    for a in audits:
        for issue in a.issues:
            kind = "fake_green" if "verified 但没有" in issue else "frontmatter"
            gaps.append(Gap(
                kind=kind,
                severity=BLOCKING if kind == "fake_green" else WARNING,
                table=f"{a.material}/{a.file}",
                message=f"{a.file}：{issue}",
                fix_hint="补 second_source 字段，或把状态改回 single_source",
            ))
    return gaps


# --- 静态覆盖分析 ----------------------------------------------------------

def _enum_domain(spec: WorkflowSpec, know: Knowledge, var: str) -> list[str]:
    """某个枚举输入的全部取值（用来枚举插值表该有哪些分组）。"""
    from .runner import _enum_options  # 避免循环导入
    idef = spec.input(var)
    return _enum_options(idef, know, spec) if idef else []


def grid_gaps(spec: WorkflowSpec, know: Knowledge) -> list[Gap]:
    """插值表的网格空洞。"""
    from . import tables as _tables

    gaps: list[Gap] = []
    for step in spec.steps:
        if step.kind != "table_interp":
            continue
        tbl = know.table(spec.material, step.require("table"))
        group_tpl = step.require("group")
        rows_key = step.require("rows_key")
        x_field, y_field, z_field = step.require("x"), step.require("y"), step.require("z")

        # group 模板里的变量（如 belt_type_{belt_type}）
        var = None
        for name in _tables._PLACEHOLDER.findall(group_tpl):
            var = name
            break
        values = _enum_domain(spec, know, var) if var else [None]

        for val in values:
            env = {var: val} if var else {}
            try:
                group = _tables.walk(tbl, group_tpl, env)
            except DataMissing:
                gaps.append(Gap(
                    kind="empty_group", severity=BLOCKING,
                    table=f"{spec.material}/{tbl.name}.yaml",
                    message=f"{tbl.name}.yaml 里完全没有 {val} 的数据分组",
                    coords={var: val},
                    fix_hint=f"新增分组 {group_tpl.format(**env)}",
                ))
                continue

            rows = (group or {}).get(rows_key) or []
            if not rows:
                gaps.append(Gap(
                    kind="empty_group", severity=BLOCKING,
                    table=f"{spec.material}/{tbl.name}.yaml",
                    message=f"{val} 分组下没有任何数据行",
                    coords={var: val},
                    fix_hint=f"在 {group_tpl.format(**env)}.{rows_key} 下补数据行",
                ))
                continue

            # 所有行上出现过的 y 值的并集 —— 完整的表应当每行都覆盖
            all_y = sorted({float(p[y_field]) for r in rows
                            for p in (r.get("points") or []) if y_field in p})
            for row in rows:
                xv = row.get(x_field)
                have = {float(p[y_field]) for p in (row.get("points") or []) if y_field in p}
                missing = [y for y in all_y if y not in have]
                if missing:
                    gaps.append(Gap(
                        kind="grid_hole", severity=WARNING,
                        table=f"{spec.material}/{tbl.name}.yaml",
                        message=(f"{val} 型 {x_field}={xv} 这一行缺 {y_field} = "
                                 f"{', '.join(_fmt(m) for m in missing)}"),
                        coords={var: val, x_field: xv, f"{y_field}_missing": missing},
                        fix_hint=f"补这些 {y_field} 对应的 {z_field} 值",
                        target={
                            "table": tbl.name,
                            "group": group_tpl.format(**env),
                            "rows_key": rows_key,
                            "x_field": x_field, "x_value": xv,
                            "y_field": y_field, "z_field": z_field,
                            "y_missing": missing,
                        },
                    ))
    return gaps


def _fmt(v: float) -> str:
    return f"{v:g}"


# --- 动态可达性探测 --------------------------------------------------------

def probe_gaps(spec: WorkflowSpec, know: Knowledge) -> tuple[list[Gap], list[dict]]:
    """按 spec.probe 的网格真跑引擎，报出哪些工况组合算不出来。

    返回 (gaps, 明细表)。明细表用于界面上画一张"能不能选"的矩阵。
    """
    from .runner import run

    cfg = getattr(spec, "probe", None) or {}
    base = dict(cfg.get("base") or {})
    sweep = dict(cfg.get("sweep") or {})
    cases = list(cfg.get("cases") or [])
    if not sweep and not cases:
        return [], []

    gaps: list[Gap] = []
    matrix: list[dict] = []

    # 两种写法：sweep 做笛卡尔积（线性工作流够用）；
    # cases 逐条列举（分支工作流必需——各分支要的参数根本不是一套）
    plans: list[tuple[dict, dict, str]] = []
    if sweep:
        keys = list(sweep)
        for combo in itertools.product(*(sweep[k] for k in keys)):
            marker = dict(zip(keys, combo))
            plans.append(({**base, **marker}, marker,
                          " · ".join(f"{k}={v}" for k, v in marker.items())))
    for case in cases:
        case = dict(case)
        name = str(case.pop("name", "")) or " · ".join(
            f"{k}={v}" for k, v in case.items())
        plans.append(({**base, **case}, {"case": name}, name))

    for values, marker, label in plans:
        try:
            trace = run(spec, values, know)
        except (InputError, SpecError) as exc:
            matrix.append({"combo": marker, "status": "input_error",
                           "message": str(exc)})
            continue
        except NeedsChoice:
            matrix.append({"combo": marker, "status": "needs_choice",
                           "message": "需要人工决策"})
            continue
        except MDSError as exc:
            matrix.append({"combo": marker, "status": "error",
                           "message": str(exc)})
            continue

        entry = {"combo": marker, "status": trace.status, "message": ""}
        if trace.status == "data_missing" and trace.blocker:
            entry["message"] = trace.blocker.get("message", "")
            entry["gap"] = trace.blocker.get("gap", "")
            failing = next((s for s in trace.steps if s["status"] == "data_missing"), None)
            gaps.append(Gap(
                kind="unreachable", severity=BLOCKING,
                table=str(trace.blocker.get("table") or ""),
                message=f"工况 {label} 算不出来：{entry['message']}",
                coords=dict(marker),
                fix_hint=str(trace.blocker.get("gap") or "")
                         or (failing or {}).get("name_zh", ""),
                target=_fill_target(spec, failing, trace, values),
            ))
        elif trace.status == "no_solution" and trace.blocker:
            entry["message"] = trace.blocker.get("message", "")
            entry["remedy"] = trace.blocker.get("remedy", "")
            gaps.append(Gap(
                kind="no_solution", severity=WARNING,
                table=str(trace.blocker.get("table") or ""),
                message=f"工况 {label} 在该规格系列内无解：{entry['message']}",
                coords=dict(marker),
                fix_hint=str(trace.blocker.get("remedy") or ""),
            ))
        elif trace.status == "check_failed":
            failed = [c["name_zh"] for c in trace.checks if not c["detail"].get("passed")]
            entry["message"] = "校核不通过：" + "、".join(failed)
        matrix.append(entry)

    return gaps, matrix


def _fill_target(spec: WorkflowSpec, failing: dict | None, trace, combo: dict) -> dict:
    """把失败步骤翻译成"该往哪张表的哪一行补哪些点"。"""
    if not failing:
        return {}
    step = spec.step(failing["id"])
    if step is None or step.kind != "table_interp":
        return {}

    from . import tables as _tables
    env = {**trace.inputs, **combo}
    coords = (trace.blocker or {}).get("coords") or {}
    x_field, y_field = step.require("x"), step.require("y")
    try:
        group = _tables.render_path(step.require("group"), env)
    except Exception:
        return {}

    missing = []
    if y_field in coords:
        missing = [coords[y_field]]
    return {
        "table": step.require("table"),
        "group": group,
        "rows_key": step.require("rows_key"),
        "x_field": x_field,
        "x_value": coords.get(x_field, env.get(x_field)),
        "y_field": y_field,
        "z_field": step.require("z"),
        "y_missing": missing,
    }


# --- 汇总 ------------------------------------------------------------------

def audit_material(material: str, know: Knowledge | None = None,
                   spec: WorkflowSpec | None = None, *, probe: bool = True) -> dict:
    """一个物料的完整体检报告。"""
    from .spec import load as load_spec

    know = know or Knowledge()
    audits = [audit_table(know.table(material, n)) for n in know.tables_of(material)]
    gaps = frontmatter_gaps(audits)
    matrix: list[dict] = []

    if spec is None:
        try:
            spec = load_spec(material)
        except SpecError:
            spec = None

    if spec is not None:
        gaps += grid_gaps(spec, know)
        if probe:
            pg, matrix = probe_gaps(spec, know)
            gaps += pg

    by_sev = {BLOCKING: 0, WARNING: 0, INFO: 0}
    for g in gaps:
        by_sev[g.severity] = by_sev.get(g.severity, 0) + 1

    # 无解与校核不通过都算"引擎给出了结论"，只有数据缺失才是真的算不出来
    reachable = sum(1 for m in matrix
                    if m["status"] in ("ok", "check_failed", "no_solution"))
    return {
        "material": material,
        "has_workflow": spec is not None,
        "tables": [a.to_dict() for a in audits],
        "gaps": [g.to_dict() for g in gaps],
        "counts": {
            "tables": len(audits),
            "verified": sum(1 for a in audits if a.confidence == "verified"),
            "can_be_verified": sum(1 for a in audits if a.can_be_verified),
            **by_sev,
        },
        "probe": {
            "total": len(matrix),
            "reachable": reachable,
            "matrix": matrix,
        },
    }

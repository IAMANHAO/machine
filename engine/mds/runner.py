"""mds.runner —— 确定性工作流解释器

输入相同、缓存版本相同 → 输出逐位相同。AI 不参与这里的任何一步。

每个步骤产出一条 StepTrace（公式 / 代入 / 结果 / 信源 / 置信度），
阶段 3 计算卡片、阶段 4 校核项、阶段 5 表 1 汇总全部由它渲染，
不存在第二份"展示用数据"。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from . import expr as _expr
from . import tables as _tables
from .errors import DataMissing, InputError, MDSError, NeedsChoice, NoSolution, SpecError
from .knowledge import Knowledge, lowest_confidence
from .rounding import round_to_series
from .spec import InputDef, StepDef, WorkflowSpec

_TEMPLATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

OK = "ok"
DATA_MISSING = "data_missing"
NO_SOLUTION = "no_solution"
NEEDS_CHOICE = "needs_choice"
CHECK_FAILED = "check_failed"
SKIPPED = "skipped"
# 与 SKIPPED 区分：not_applicable 是"这条分支用不上这一步"，
# skipped 是"前面出问题所以没跑到"。混为一谈会让人以为流程出了错。
NOT_APPLICABLE = "not_applicable"


@dataclass
class StepTrace:
    id: str
    name_zh: str
    kind: str
    status: str = OK
    formula: str = ""
    substitution: str = ""
    inputs: dict = field(default_factory=dict)
    value: Any = None
    value_display: str = ""
    unit: str = ""
    outputs: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    note: str = ""
    detail: dict = field(default_factory=dict)
    error: dict | None = None


@dataclass
class SelectionTrace:
    material: str
    name_zh: str
    standard: str
    status: str = OK
    inputs: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    result: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    confidence: str = "unknown"
    warnings: list = field(default_factory=list)
    blocker: dict | None = None

    @property
    def checks(self) -> list:
        # 未执行的步骤不计入校核项——把 skipped 显示成"不通过"是危险的误导
        return [s for s in self.steps
                if s.get("kind") == "check"
                and s.get("status") not in (SKIPPED, NOT_APPLICABLE)]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = self.checks
        return d


# --- 输入校验 --------------------------------------------------------------

def validate_inputs(spec: WorkflowSpec, values: dict, knowledge: Knowledge) -> dict:
    """校验并归一用户输入。AI 给的建议值走的是同一条通道。"""
    env: dict[str, Any] = {}
    missing: list[str] = []

    for idef in spec.inputs:
        raw = values.get(idef.id)
        if raw is None or raw == "":
            if idef.default is not None:
                env[idef.id] = idef.default
            elif _is_required(idef, values) and not idef.one_of:
                missing.append(idef.label())
            continue
        env[idef.id] = _coerce(idef, raw, spec, knowledge)

    # 二选一分组：每组至少给一个
    groups: dict[str, list[InputDef]] = {}
    for idef in spec.inputs:
        if idef.one_of:
            groups.setdefault(idef.one_of, []).append(idef)
    for members in groups.values():
        if not any(m.id in env for m in members):
            missing.append(" 或 ".join(m.label() for m in members))

    if missing:
        raise InputError("缺少必需参数：" + "；".join(missing))
    return env


def _is_required(idef: InputDef, values: dict) -> bool:
    """required_when 让分支型工作流只要求本分支真正用得到的参数。"""
    if idef.required_when:
        try:
            return bool(_expr.evaluate(idef.required_when, _as_env(values)))
        except Exception:
            # 条件依赖的参数自己还没填，此刻先不强制
            return False
    return idef.required


def _as_env(values: dict) -> dict:
    """把原始输入转成表达式可用的环境：数值转 float，其余保持字符串。"""
    env = {}
    for k, v in values.items():
        if v is None or v == "":
            continue
        try:
            env[k] = float(v)
        except (TypeError, ValueError):
            env[k] = v
    return env


def _coerce(idef: InputDef, raw: Any, spec: WorkflowSpec, knowledge: Knowledge) -> Any:
    if idef.type == "enum":
        allowed = _enum_options(idef, knowledge, spec)
        if allowed and str(raw) not in allowed:
            raise InputError(
                f"{idef.label()} 的取值 {raw!r} 不在允许范围内，可选：{', '.join(allowed)}",
                param=idef.id, value=raw)
        return str(raw)
    if idef.type == "text":
        return str(raw)

    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise InputError(f"{idef.label()} 需要一个数值，收到 {raw!r}",
                         param=idef.id, value=raw) from None
    lo, hi = idef.domain.get("min"), idef.domain.get("max")
    if lo is not None and val < float(lo):
        raise InputError(f"{idef.label()} = {val:g} 小于允许下限 {float(lo):g}",
                         param=idef.id, value=val, domain=idef.domain)
    if hi is not None and val > float(hi):
        raise InputError(f"{idef.label()} = {val:g} 超出允许上限 {float(hi):g}",
                         param=idef.id, value=val, domain=idef.domain)
    return val


def _option_pair(raw: Any) -> tuple[str, str]:
    """选项可以写成裸值，也可以写成 {value, label} 以便给界面中文标签。"""
    if isinstance(raw, dict):
        value = str(raw.get("value", ""))
        return value, str(raw.get("label") or value)
    return str(raw), str(raw)


def _enum_options(idef: InputDef, knowledge: Knowledge, spec: WorkflowSpec) -> list[str]:
    if idef.options:
        return [_option_pair(o)[0] for o in idef.options]
    src = idef.options_from
    if not src:
        return []
    tbl = knowledge.table(spec.material, src["table"])
    node = _tables.walk(tbl, src["path"])
    if isinstance(node, dict):
        return [str(k) for k in node]
    return [str(v) for v in node]


def enum_choices(spec: WorkflowSpec, knowledge: Knowledge) -> dict[str, list[dict]]:
    """给前端阶段 2 表单用的枚举选项（含中文标签）。"""
    out: dict[str, list[dict]] = {}
    for idef in spec.inputs:
        if idef.type != "enum":
            continue
        if idef.options:
            out[idef.id] = [{"value": v, "label": lb}
                            for v, lb in map(_option_pair, idef.options)]
            continue
        src = idef.options_from
        if not src:
            continue
        tbl = knowledge.table(spec.material, src["table"])
        node = _tables.walk(tbl, src["path"])
        label_key = src.get("label", "name_zh")
        if isinstance(node, dict):
            out[idef.id] = [
                {"value": str(k),
                 "label": (v.get(label_key) if isinstance(v, dict) else None) or str(k)}
                for k, v in node.items()
            ]
    return out


# --- 主执行循环 ------------------------------------------------------------

def run(spec: WorkflowSpec, values: dict, knowledge: Knowledge | None = None,
        choices: dict | None = None) -> SelectionTrace:
    knowledge = knowledge or Knowledge()
    choices = choices or {}

    env = validate_inputs(spec, values, knowledge)
    trace = SelectionTrace(material=spec.material, name_zh=spec.name_zh,
                           standard=spec.standard, inputs=dict(env))
    sources: list[dict] = []
    halted = False

    for step in spec.steps:
        if halted:
            trace.steps.append(asdict(StepTrace(
                id=step.id, name_zh=step.name_zh, kind=step.kind, status=SKIPPED,
                note="前序步骤未能完成，本步未执行")))
            continue

        guard = step.get("when")
        if guard and not _expr.evaluate(guard, env):
            trace.steps.append(asdict(StepTrace(
                id=step.id, name_zh=step.name_zh, kind=step.kind,
                status=NOT_APPLICABLE,
                note=step.get("when_note", "") or "本条工况分支用不到这一步")))
            continue

        try:
            st = _run_step(step, env, spec, knowledge, choices, sources)
        except NeedsChoice as exc:
            st = StepTrace(id=step.id, name_zh=step.name_zh, kind=step.kind,
                           status=NEEDS_CHOICE, note=exc.reason or str(exc),
                           error=exc.as_dict())
            trace.status, trace.blocker, halted = NEEDS_CHOICE, exc.as_dict(), True
        except NoSolution as exc:
            # 与数据缺失区分开：这是选型结论，指引是改设计而不是补知识库
            st = StepTrace(id=step.id, name_zh=step.name_zh, kind=step.kind,
                           status=NO_SOLUTION, note=exc.remedy or str(exc),
                           error=exc.as_dict())
            trace.status, trace.blocker, halted = NO_SOLUTION, exc.as_dict(), True
        except (DataMissing, MDSError) as exc:
            st = StepTrace(id=step.id, name_zh=step.name_zh, kind=step.kind,
                           status=DATA_MISSING, note=str(exc), error=exc.as_dict())
            trace.status, trace.blocker, halted = DATA_MISSING, exc.as_dict(), True
        else:
            env.update(st.outputs)
            # 校核不通过要如实报出，但后续步骤继续算，好让用户一次看到全部问题
            if st.status == CHECK_FAILED and trace.status == OK:
                trace.status = CHECK_FAILED

        trace.steps.append(asdict(st))

    trace.outputs = dict(env)
    trace.sources = _dedup_sources(sources)
    trace.confidence = lowest_confidence([s.get("confidence") for s in trace.sources])
    if not halted:
        trace.result = _render_result(spec, env)

    # 非随包工作流：这条警告要排在最前面，且比数据表那条更重。
    # 数据表未核验，至少流程本身是随包核过的；自建流程的公式没有那层保障。
    if spec.provenance == "user_guided":
        trace.confidence = "unknown"
        trace.warnings.append(_guided_warning(spec))
    elif spec.provenance == "ai_generated":
        # 旧版"AI 一次性起草"留下的物料，只读兼容，不再新建。
        trace.confidence = "unknown"
        trace.warnings.append(
            f"⚠ 本物料的**选型流程**由旧版的「AI 一次性起草」生成"
            f"（{spec.generated_by or '未知模型'}），**没有任何依据，也没有人核对过公式**。"
            "现在的引导式选型会先取证依据、再让你确认整套公式——"
            "建议用引导式重做一遍，把这份替换掉。**不要拿这份结果定稿。**")
    elif trace.confidence != "verified":
        trace.warnings.append(
            "本次选型引用的数据表中存在未经双源核验的条目（🟡 single_source），"
            "结果仅供初步设计参考，正式投产前请对照标准原件复核。")
    return trace


_BASIS_STATUS_ZH = {
    "cross_checked": "两处独立来源互证",
    "trusted": "可信站点单一来源",
    "single_source": "单一来源",
    "unverifiable_claim": "无法自动取证，由你自行核对",
}


def _guided_warning(spec: WorkflowSpec) -> str:
    """引导式选型的结果警告。

    它和 ai_generated 那条的区别不是措辞软一点，而是**说的是另一件事**：
    依据是用户确认的、公式是用户过目的、数值是用户填的或引擎算的。
    但这仍然不等于核验 —— 取证只证明那份文件里确实有这个标准号，
    不证明这个公式适用于用户的工况。这条界限必须写在结果里。
    """
    basis = spec.basis or {}
    claim = str(basis.get("claim") or "").strip() or "未记录依据"
    status = _BASIS_STATUS_ZH.get(str(basis.get("status") or ""), "未取证")
    urls = [u for u in (basis.get("urls") or []) if u][:3]
    tail = ("　引用：" + "、".join(urls)) if urls else ""
    return (
        f"⚠ 本物料的**选型流程**是在线引导下组装的。依据：{claim}（取证：{status}）。"
        "整套公式已由你过目确认，数值全部由你填写或由引擎算出，AI 没有提供任何数值。"
        "但这**不等于经过核验**——取证只证明那份文件里确实有这个标准号，"
        "不证明这个公式适用于你的工况。正式定稿前请对照依据原文复核。" + tail)


def _run_step(step: StepDef, env: dict, spec: WorkflowSpec, knowledge: Knowledge,
              choices: dict, sources: list) -> StepTrace:
    st = StepTrace(id=step.id, name_zh=step.name_zh or step.id, kind=step.kind,
                   unit=step.get("unit", ""), note=step.note)
    if step.source:
        st.source.update(step.source)
    _HANDLERS[step.kind](step, env, spec, knowledge, choices, st, sources)
    if isinstance(st.value, (int, float)) and not isinstance(st.value, bool):
        st.value_display = _expr.fmt_num(st.value)
    elif st.value is not None:
        st.value_display = str(st.value)
    return st


# --- 各 kind 的处理 --------------------------------------------------------

def _h_formula(step, env, spec, knowledge, choices, st, sources):
    src = step.require("expr")
    names = sorted(_expr.referenced_names(src))
    st.inputs = {n: env[n] for n in names if n in env}
    st.formula = f"{step.outputs[0]} = {_pretty(src)}"
    st.substitution = _expr.substitute(src, env)
    st.value = _expr.evaluate(src, env)
    st.outputs = {step.outputs[0]: st.value}


def _h_table_lookup(step, env, spec, knowledge, choices, st, sources):
    tbl = knowledge.table(spec.material, step.require("table"))
    key = step.require("key")
    st.value = _tables.lookup(tbl, key, env)
    st.outputs = {step.outputs[0]: st.value}
    rendered = _tables.render_path(key, env)
    st.formula = f"查表 {tbl.name}.yaml → {rendered}"
    st.substitution = rendered
    _attach_source(st, tbl, sources)


def _h_table_pick(step, env, spec, knowledge, choices, st, sources):
    """从决策表里取一整组推荐字段。

    润滑油这类表返回的不是一个数，而是一组建议（ISO VG 候选、基础油、添加剂、
    典型产品、注意事项）。硬塞进 table_lookup 只会把它们压成字符串。
    """
    tbl = knowledge.table(spec.material, step.require("table"))
    key = step.require("key")
    node = _tables.walk(tbl, key, env)
    if not isinstance(node, dict):
        raise DataMissing(
            f"{tbl.name}.yaml 的 {_tables.render_path(key, env)} 不是一组推荐值",
            table=f"{spec.material}/{tbl.name}", path=key)

    fields = step.require("fields")
    picked, missing = {}, []
    for src_key, out_name in fields.items():
        if src_key not in node:
            missing.append(src_key)
            continue
        picked[out_name] = node[src_key]
    if missing and not step.get("allow_partial"):
        raise DataMissing(
            f"{tbl.name}.yaml 的 {_tables.render_path(key, env)} 缺字段："
            f"{'、'.join(missing)}",
            table=f"{spec.material}/{tbl.name}", path=key,
            available=sorted(map(str, node)),
            gap=f"需要在该节点下补 {'、'.join(missing)}")

    # 列表字段额外给一个 <名>_first：推荐往往是个区间（VG 150/220/320），
    # 但采购关键词只能带一个值
    for name, val in list(picked.items()):
        if isinstance(val, list) and val:
            picked[f"{name}_first"] = val[0]

    st.outputs = picked
    st.value = _fmt_pick({k: v for k, v in picked.items()
                          if not k.endswith("_first")})
    rendered = _tables.render_path(key, env)
    st.formula = f"查决策表 {tbl.name}.yaml → {rendered}"
    st.substitution = rendered
    st.detail = {"picked": picked, "path": rendered}
    _attach_source(st, tbl, sources)


def _fmt_pick(picked: dict) -> str:
    parts = []
    for k, v in picked.items():
        parts.append(f"{k}={_fmt_val(v)}")
    return "；".join(parts)


def _fmt_val(v) -> str:
    if isinstance(v, list):
        return " / ".join(_expr.fmt_num(x) for x in v)
    if v is None:
        return "—"
    return _expr.fmt_num(v)


def _h_table_interp(step, env, spec, knowledge, choices, st, sources):
    tbl = knowledge.table(spec.material, step.require("table"))
    x_field, y_field, z_field = step.require("x"), step.require("y"), step.require("z")
    x_val, y_val = env[x_field], env[y_field]
    st.inputs = {x_field: x_val, y_field: y_val}
    st.value = _tables.interp2d(
        tbl, step.require("group"), step.require("rows_key"),
        x_field, y_field, z_field, float(x_val), float(y_val), env)
    st.outputs = {step.outputs[0]: st.value}
    st.formula = f"查表插值 {tbl.name}.yaml（{x_field} × {y_field} → {z_field}）"
    st.substitution = f"{x_field}={_expr.fmt_num(x_val)}, {y_field}={_expr.fmt_num(y_val)}"
    _attach_source(st, tbl, sources)


def _h_bucket(step, env, spec, knowledge, choices, st, sources):
    src = step.require("value")
    val = _resolve(src, env, where=f"分档步骤 {step.id} 的 value")
    st.inputs = {str(src): val}
    st.value = _tables.bucket(float(val), step.require("bins"))
    st.outputs = {step.outputs[0]: st.value}
    st.formula = f"{src} = {_expr.fmt_num(val)} → 落入分档"
    st.substitution = str(st.value)


def _h_round(step, env, spec, knowledge, choices, st, sources):
    tbl = knowledge.table(spec.material, step.require("table"))
    ser = _tables.series(tbl, step.require("path"), env)
    src = step.require("value")
    raw = _resolve(src, env, where=f"圆整步骤 {step.id} 的 value")
    what = step.get("what", step.name_zh or step.outputs[0])
    info = round_to_series(float(raw), ser, step.get("mode", "nearest_above"),
                           on_exceed=step.get("on_exceed", "clamp"),
                           what=what, table=f"{spec.material}/{tbl.name}")
    st.value = info["value"]
    st.outputs = {step.outputs[0]: st.value}
    st.detail = info
    st.formula = (f"圆整到标准系列（{tbl.name}.yaml，{info['series_size']} 档，"
                  f"模式 {info['mode']}）")
    st.substitution = f"{_expr.fmt_num(raw)} → {_expr.fmt_num(st.value)}"
    _attach_source(st, tbl, sources)


def _h_row_select(step, env, spec, knowledge, choices, st, sources):
    """在有序行集里挑第一个满足阈值的行，取它整行的多个字段。

    螺栓按 As 选规格、键按长度选标准键长、轴承按 C 选型号、O 形圈按内径选规格——
    都是这同一个动作。用 round_to_series 做不到：它只圆整扁平数字列表，
    拿不回同一行的其它字段。绕开的办法是把同一份数据存两遍（一份扁平系列 +
    一份反查字典），那正是这个项目最该避免的事。

    **没有"取最接近的一行"这个退路。** 阈值选不中就是选不中：
    给一个比所需规格更小的螺栓，比停下来说"系列内无解"危险得多。
    """
    tbl = knowledge.table(spec.material, step.require("table"))
    rows_key = step.require("rows_key")
    candidates = _tables.rows(tbl, rows_key, env)

    raw_conds = step.require("where")
    if isinstance(raw_conds, dict):
        raw_conds = [raw_conds]
    conds = []
    for c in raw_conds:
        if "field" not in c or "value" not in c:
            raise SpecError(f"步骤 {step.id} 的筛选条件缺 field 或 value：{c!r}")
        conds.append({
            "field": c["field"],
            "op": c.get("op", ">="),
            "value": _resolve(c["value"], env,
                              where=f"选行步骤 {step.id} 的 {c['field']} 条件"),
        })

    order_by = step.get("order_by")
    hit = _tables.rows_where(candidates, conds, order_by=order_by,
                             table=tbl, path=rows_key)
    what = step.get("what", step.name_zh or step.outputs[0])

    if not hit:
        _no_row(step, tbl, rows_key, candidates, conds, what, spec)

    row = hit[0]
    fields = step.require("fields")
    picked, missing = {}, []
    for src_key, out_name in fields.items():
        if src_key not in row or row[src_key] is None:
            missing.append(src_key)
            continue
        picked[out_name] = row[src_key]
    if missing:
        raise DataMissing(
            f"{tbl.name}.yaml 选中的那一行缺字段：{'、'.join(missing)}",
            table=f"{spec.material}/{tbl.name}", path=rows_key,
            available=sorted(map(str, row)),
            gap=f"需要给 {rows_key} 里 {row.get('label', row)} 这一行补 "
                f"{'、'.join(missing)}")

    st.outputs = picked
    st.value = row.get("label") or _fmt_pick(picked)
    cond_txt = "、".join(f"{c['field']} {c['op']} {_expr.fmt_num(c['value'])}"
                         for c in conds)
    st.formula = (f"在 {tbl.name}.yaml 的 {rows_key} 里选满足 {cond_txt} 的"
                  f"{'最小' if order_by else '第一'}一行"
                  + (f"（按 {order_by} 排序）" if order_by else ""))
    st.substitution = cond_txt + f" → {st.value}"
    st.detail = {"picked": picked, "row": row, "conditions": conds,
                 "candidates": len(candidates), "matched": len(hit)}
    _attach_source(st, tbl, sources)


def _no_row(step, tbl, rows_key, candidates, conds, what, spec) -> None:
    """一行都没选中。是"系列内无解"还是"表缺数据"？两者给用户的方向完全相反。

    阈值条件卡在了系列上/下限 → 无解，该换设计。
    其它情况（等值筛选没命中、行里就没这个字段）→ 缺数据，该补表。
    """
    for c in conds:
        if c["op"] not in (">=", ">", "<=", "<"):
            continue
        vals = [float(r[c["field"]]) for r in candidates
                if c["field"] in r and r[c["field"]] is not None]
        if not vals:
            continue
        above = c["op"] in (">=", ">")
        bound = max(vals) if above else min(vals)
        if (above and c["value"] > bound) or (not above and c["value"] < bound):
            word = "上限" if above else "下限"
            raise NoSolution(
                f"所需{what}（{c['field']} {c['op']} {_expr.fmt_num(c['value'])}）"
                f"超出可选范围{word} {_expr.fmt_num(bound)}，本系列内无解",
                table=f"{spec.material}/{tbl.name}", what=what,
                required=c["value"], limit=bound,
                remedy="改用更大规格 / 更高强度等级的型号，或降低载荷",
            )

    raise DataMissing(
        f"{tbl.name}.yaml 的 {rows_key} 里没有满足条件的行",
        table=f"{spec.material}/{tbl.name}", path=rows_key,
        gap=f"需要在 {rows_key} 下补满足 "
            + "、".join(f"{c['field']} {c['op']} {_expr.fmt_num(c['value'])}"
                        for c in conds) + " 的行",
    )


def _h_check(step, env, spec, knowledge, choices, st, sources):
    op = step.get("op", "<=")
    val_src, lim_src = step.require("value"), step.require("limit")
    val = _resolve(val_src, env, where=f"校核步骤 {step.id} 的 value")
    lim = _resolve(lim_src, env, where=f"校核步骤 {step.id} 的 limit")
    passed = bool(_expr.evaluate(f"a {op} b", {"a": val, "b": lim}))

    st.inputs = {"value": val, "limit": lim}
    st.value = passed
    st.formula = f"{_pretty(str(val_src))} {op} {_pretty(str(lim_src))}"
    st.substitution = f"{_expr.fmt_num(val)} {op} {_expr.fmt_num(lim)}"
    st.detail = {"passed": passed, "op": op, "value": val, "limit": lim,
                 "unit": step.get("unit", "")}
    if not passed:
        st.status = CHECK_FAILED
        st.detail["remedy"] = step.get("on_fail", "")
        st.note = step.get("on_fail", "") or st.note


def _h_select(step, env, spec, knowledge, choices, st, sources):
    from_input = step.get("from_input")
    picked = choices.get(step.id)
    if picked is None and from_input:
        picked = env.get(from_input)
    cands = _candidates(step.require("candidates"), spec, knowledge, env, sources, st)

    if picked is None or picked == "":
        raise NeedsChoice(
            f"{st.name_zh} 需要决策", step=step.id, candidates=cands,
            recommended=step.get("recommended"),
            reason=step.get("reason") or f"{st.name_zh} 无法由现有数据唯一确定，需工程师确认")

    valid = [c["value"] for c in cands]
    if valid and str(picked) not in valid:
        # **这不是数据缺口。** 报成 DataMissing 会让界面说"去知识库补这张表"，
        # 把人指向一个根本不存在的问题——表好好的，只是叫法对不上。
        # 退回成"还需要决策"：界面照常把候选摆出来让人重选，流程不中断。
        raise NeedsChoice(
            f"{st.name_zh}：{picked!r} 不在候选范围内，请从下面重新选一个",
            step=step.id, candidates=cands,
            recommended=step.get("recommended"),
            reason=f"你给的 {picked!r} 与候选的叫法对不上。"
                   f"可选：{'、'.join(valid)}",
            given=str(picked))
    st.value = str(picked)
    st.outputs = {step.outputs[0]: st.value}
    st.formula = f"{st.name_zh}（由用户指定）"
    st.substitution = str(picked)
    st.detail = {"candidates": cands, "chosen": st.value, "by": "user"}


def _h_text(step, env, spec, knowledge, choices, st, sources):
    """渲染一段模板文本。

    分支型工作流需要给结果表一个人能读的标签（"链条油" 而不是 "chain"），
    以及拼采购关键词。用专门的一步，好过把这些塞进表达式求值器。
    """
    tpl = step.require("template")
    st.value = _render_template(str(tpl), env)
    st.outputs = {step.outputs[0]: st.value}
    st.formula = f"{step.outputs[0]} = {tpl}"
    st.substitution = st.value


def _h_default(step, env, spec, knowledge, choices, st, sources):
    from_input = step.get("from_input", step.outputs[0])
    given = env.get(from_input)
    if given is not None and given != "":
        st.value = given
        st.formula = f"{st.name_zh}（用户提供）"
        st.substitution = _expr.fmt_num(st.value)
        st.detail = {"by": "user"}
    else:
        src = step.require("fallback")
        st.value = _expr.evaluate(src, env)
        st.formula = f"{step.outputs[0]} = {_pretty(src)}（未提供，按推荐式取值）"
        st.substitution = _expr.substitute(src, env)
        st.detail = {"by": "engine_default"}
        st.inputs = {n: env[n] for n in sorted(_expr.referenced_names(src)) if n in env}
    st.outputs = {step.outputs[0]: st.value}


_HANDLERS = {
    "formula": _h_formula,
    "table_lookup": _h_table_lookup,
    "table_pick": _h_table_pick,
    "table_interp": _h_table_interp,
    "bucket": _h_bucket,
    "round_to_series": _h_round,
    "row_select": _h_row_select,
    "check": _h_check,
    "select": _h_select,
    "default": _h_default,
    "text": _h_text,
}


# --- 辅助 ------------------------------------------------------------------

def _candidates(cs, spec: WorkflowSpec, knowledge: Knowledge, env: dict,
                sources: list, st: StepTrace) -> list[dict]:
    if isinstance(cs, list):
        # 内联候选与枚举输入的 options 用**同一套**写法：裸值或 {value, label}。
        # 早先这里写的是 str(c)，把整个 {value, label} 字典字符串化成
        # "{'value': '烧结钕铁硼', ...}" —— 于是用户哪怕选了候选里明明有的值
        # 也永远对不上，还被报成"该工况点没有数据"。两处写法必须一致。
        return [{"value": v, "label": lb} for v, lb in map(_option_pair, cs)]
    tbl = knowledge.table(spec.material, cs["table"])
    _attach_source(st, tbl, sources)
    node = _tables.walk(tbl, cs["path"], env)
    label_key = cs.get("label_field", "name_zh")
    details = cs.get("detail_fields") or []
    if not isinstance(node, dict):
        return [{"value": str(v), "label": str(v)} for v in node]
    out = []
    for key, val in node.items():
        row = {"value": str(key),
               "label": (val.get(label_key) if isinstance(val, dict) else None) or str(key)}
        if isinstance(val, dict) and details:
            row["detail"] = {d: val.get(d) for d in details if d in val}
        out.append(row)
    return out


def _attach_source(st: StepTrace, tbl, sources: list) -> None:
    info = tbl.source_info()
    st.source.setdefault("ref", info["data_source"])
    st.source.update({k: v for k, v in info.items() if k != "data_source"})
    sources.append(info)


def _dedup_sources(sources: list) -> list:
    seen, out = set(), []
    for s in sources:
        key = s.get("table_file")
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _is_expr(src: Any) -> bool:
    """含运算符/括号才当表达式，否则当变量名。"""
    return isinstance(src, str) and bool(re.search(r"[-+*/()^ ]", src))


def _resolve(src: Any, env: dict, *, where: str) -> Any:
    """把规格里的 value/limit 字段解析成数值。

    可能是三种东西，按此顺序判定：
      1. 字面量数字（YAML 里的 6，或被引号包起来的 "6"）
      2. 表达式（含运算符，如 "0.7 * (d1 + d2)"）
      3. 变量名（如 "v_max"）
    变量名不在上下文里时给出可读报错，而不是裸 KeyError。
    """
    if isinstance(src, (int, float)) and not isinstance(src, bool):
        return src
    if not isinstance(src, str):
        raise SpecError(f"{where} 的取值 {src!r} 既不是数值也不是表达式")

    text = src.strip()
    try:
        return float(text)
    except ValueError:
        pass
    if _is_expr(text):
        return _expr.evaluate(text, env)
    if text in env:
        return env[text]
    raise SpecError(
        f"{where} 引用了变量 {text!r}，但此刻上下文里没有它"
        f"（可用：{', '.join(sorted(k for k in env if not k.startswith('_')))}）")


def _pretty(src: str) -> str:
    return src.replace("**", "^").replace(" * ", " × ")


def _render_template(text: str, env: dict) -> str:
    """把 {name} 替换成 env 里的值；列表渲染成 "150 / 220 / 320"。"""
    return _TEMPLATE.sub(
        lambda m: _fmt_val(env[m.group(1)]) if m.group(1) in env else m.group(0),
        text)


def _render_result(spec: WorkflowSpec, env: dict) -> list[dict]:
    """渲染阶段 5 的表 2（最终选型结果）。

    分支型工作流里，结果表会列出所有分支可能产出的字段；本次没走到的
    分支自然没有对应输出。这种行直接不显示——把 "{form}" 原样打在
    结果表里，比少一行糟糕得多。
    """
    out = []
    for row in spec.result:
        template = str(row["value"])
        needed = set(_TEMPLATE.findall(template))
        if needed and not needed <= set(env):
            continue   # 本分支产不出这些字段
        out.append({
            "label": row["label"],
            "value": _render_template(template, env),
            "unit": row.get("unit", ""),
        })
    return out

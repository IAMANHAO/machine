"""四个窄接口 —— AI 能做的全部事情。

| 接口              | 做什么                          | 未绑定 / 离线时            |
|-------------------|---------------------------------|----------------------------|
| `parse_intent`    | 自然语言 → 物料 + 已知参数      | 关键词匹配                 |
| `suggest_params`  | 缺失参数 → 建议值 + 理由        | 读规格里的 typical/default |
| `explain`         | 已算好的 StepTrace → 白话       | 关闭                       |
| `draft_workflow`  | 知识库里没有的物料 → 选型流程   | 关闭（这是在线增强）       |

四条硬边界，都是结构性的，不是靠注释或提示词约束的：

1. **AI 不做任何算术。** 这里没有一处把模型输出当成计算结果。
2. **建议值必须过校验闸门。** 每个建议值都跑一遍 `mds.runner._coerce`——
   与用户手输完全同一条通道。过不了的直接标成 rejected 返回，
   调用方拿不到一个"绕过校验的数"。
3. **建议不会自动生效。** 返回的是 suggestion，写不写进参数由人决定。
4. **起草的工作流不得携带数据。** 见下。

## draft_workflow：让 AI 生成流程，但绝不生成数据

知识库里没有的物料（磁吸铁片、导轨滑块、气缸…），在线时由 AI 起草一份
**可执行的工作流规格**，然后交给同一个确定性引擎执行——
用户照常走完整的选型流程，照常看到每一步的公式与代入值。

这件事和"让 AI 算"只有一线之隔，边界靠**两道结构性闸门**守住：

**闸门一：起草的规格必须通过 `spec.parse()`。**
与随包工作流走完全同一个解析器与静态校验——未知步骤类型、重复 id、
表达式引用了不存在的变量、白名单外的函数，一律当场拒绝。
模型编不出一个"只对它自己成立"的格式。

**闸门二：起草的规格不得引用任何数据表。**
这是最要紧的一条。系数、许用应力、标准系列这些"手册会列表的量"，
AI 一律**不准内联成数字**，必须做成用户输入，并在 hint 里写明去哪本手册查。

于是：引擎做算术（确定的事），AI 给流程（不确定的事），
**数值全部由用户从他自己的依据里填**。没有一个来路不明的数字进入计算。

代价是表单变长——用户要自己查几个系数。这个代价是对的：
一个 AI 编出来的 K=1.3 和一个抄自国标的 K=1.3，在界面上长得一模一样。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .client import AIError, Completion, Provider

# --- 提示词 ----------------------------------------------------------------

_INTENT_SYS = """你是机械设计选型助手的意图解析模块。

任务：把工程师的一句话工况描述，解析成结构化的物料与参数。

严格遵守：
- 只做解析，不做任何计算、换算、推断缺失值。
- 用户没说的参数一律不要填，不要按经验补默认值。
- 单位不换算，原样给数值；如果用户给的单位与目标单位不同，放进 notes 说明。
- 只能从给定的物料清单里选，选不出就把 material 设为 null。
- **清单里没有不等于不能选。** 选不出时，把用户想选的那个物料名填进
  unknown_material（如"磁吸铁片"、"直线导轨滑块"）——引擎会据此起草一份流程。
  不要在 notes 里写"无法完成选型"之类的话，那不是事实。

只输出 JSON，格式：
{"material": "物料id或null", "values": {"参数id": 数值或字符串},
 "unknown_material": "清单里没有时，用户想选的物料名；能匹配上就留空字符串",
 "unmatched": ["无法归类的片段"], "notes": "需要提醒工程师的话，没有就空字符串"}"""

_SUGGEST_SYS = """你是机械设计选型助手的参数建议模块。

任务：为缺失的工况参数给出**建议值**与理由，供工程师确认。

严格遵守：
- 你给的是建议，不是结论。工程师会逐项确认后才采用。
- 每一项都要给出理由，说明为什么这个取值对这个场景是合理的。
- 不确定就不要给，把该参数放进 skipped 并说明为什么需要工程师自己定。
- 枚举参数只能从给定的候选值里选。
- 数值参数必须落在给定的取值范围内。
- 不要计算任何派生量——那是计算引擎的事。

只输出 JSON，格式：
{"suggestions": {"参数id": {"value": 值, "rationale": "理由"}},
 "skipped": {"参数id": "为什么需要工程师自己定"}}"""

_EXPLAIN_SYS = """你是机械设计选型助手的解释模块。

任务：把一步已经算完的计算，用工程师能一眼看懂的白话讲清楚。

严格遵守：
- 结果已经由确定性引擎算出，你**绝不能重算、修正或质疑数值**。
- 如果你觉得数值可疑，只在末尾用一句话提示"建议人工复核"，不要给出你自己的数。
- 讲清三件事：这一步在整个选型里解决什么问题、为什么用这个公式、这个结果意味着什么。
- 不要复述代入过程，界面上已经有了。
- 三到五句话，不用小标题，不用列表。"""


# --- 阶段 0：意图解析 -------------------------------------------------------

def parse_intent(text: str, materials: list[dict], provider: Provider | None,
                 model: str, budget=None) -> dict:
    """自然语言 → {material, values}。AI 不可用时退化成关键词匹配。"""
    text = (text or "").strip()
    if not text:
        return {"material": None, "values": {}, "source": "empty", "notes": ""}

    if provider is None:
        return _intent_offline(text, materials)

    catalog = [{"id": m["id"], "name": m["name_zh"]} for m in materials
               if m.get("status") == "ready"]
    user = (f"物料清单：{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"工况描述：{text}")

    if budget:
        budget.check()
    comp = provider.chat(
        [{"role": "system", "content": _INTENT_SYS},
         {"role": "user", "content": user}],
        model=model, json_mode=True,
        max_tokens=budget.cap_tokens(600) if budget else 600)
    if budget:
        budget.record(comp.usage)

    data = comp.as_json()
    if not isinstance(data, dict):
        raise AIError("意图解析返回的不是对象", kind="bad_json")

    allowed = {m["id"] for m in catalog}
    material = data.get("material")
    unknown = ""
    if material not in allowed:
        # 认不出来不是终点。把用户想选的物料名带回去，
        # 前端据此提供"让 AI 起草这个物料的选型流程"这条路。
        unknown = str(data.get("unknown_material") or "").strip() or text
        material = None

    return {
        "material": material,
        "values": data.get("values") if isinstance(data.get("values"), dict) else {},
        "unknown_material": unknown,
        "can_draft": bool(unknown),
        "unmatched": data.get("unmatched") or [],
        "notes": str(data.get("notes") or ""),
        "source": "ai",
        "usage": comp.usage.to_dict(),
    }


def _intent_offline(text: str, materials: list[dict]) -> dict:
    """离线兜底：按物料名关键词匹配 + 抓几个明显的带单位数值。

    刻意做得很保守——宁可少认，也不要认错。认不出来就让用户自己选物料。
    """
    hit = None
    for m in materials:
        if m.get("status") != "ready":
            continue
        name = re.sub(r"（.*?）", "", m.get("name_zh", "")).strip()
        if name and name in text:
            hit = m["id"]
            break

    values: dict[str, Any] = {}
    patterns = {
        "P": r"(\d+(?:\.\d+)?)\s*(?:kw|kW|千瓦)",
        "n1": r"(\d+(?:\.\d+)?)\s*(?:r/min|rpm|转|转速)",
        "a0": r"(?:中心距|轴间距)\s*[：:]?\s*(\d+(?:\.\d+)?)",
        "i": r"(?:传动比|速比)\s*[：:]?\s*(\d+(?:\.\d+)?)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            values[key] = float(m.group(1))

    return {
        "material": hit,
        "values": values,
        # 离线时认不出物料，也如实说明这不是终点——联网绑号后可以起草。
        # 但 can_draft 保持 False：起草确实需要在线，这里不能给个点了没反应的按钮。
        "unknown_material": "" if hit else text[:40],
        "can_draft": False,
        "unmatched": [],
        "notes": ("离线模式：仅按关键词粗匹配，请核对识别结果是否正确。" if hit else
                  "离线模式认不出这个物料。可以直接从下面的物料卡片里选；"
                  "如果要选的物料不在其中，联网并绑定账号后可以让 AI 起草一份选型流程。"),
        "source": "offline",
    }


# --- 阶段 2：参数建议 -------------------------------------------------------

def suggest_params(spec, know, known: dict, provider: Provider | None,
                   model: str, budget=None) -> dict:
    """为缺失参数给建议值。

    **每个建议值都会走一遍与手输完全相同的校验闸门**，过不了的标成 rejected。
    这是结构性保证：调用方不可能从这里拿到一个绕过校验的数。
    """
    from mds.runner import _coerce, _enum_options

    missing = [i for i in spec.inputs
               if i.id not in known or known.get(i.id) in (None, "")]
    if not missing:
        return {"suggestions": {}, "skipped": {}, "rejected": {}, "source": "none"}

    if provider is None:
        return _suggest_offline(spec, know, missing)

    def describe(i):
        d = {"id": i.id, "name": i.name_zh, "unit": i.unit, "type": i.type,
             "required": i.required, "hint": i.hint}
        if i.domain:
            d["range"] = i.domain
        if i.type == "enum":
            d["options"] = [{"value": o["value"], "label": o["label"]}
                            for o in _enum_choices_for(spec, know, i)]
        if i.typical is not None:
            d["typical"] = i.typical
        return d

    user = (f"物料：{spec.name_zh}（依据 {spec.standard}）\n"
            f"已知参数：{json.dumps(known, ensure_ascii=False, default=str)}\n"
            f"待建议参数：{json.dumps([describe(i) for i in missing], ensure_ascii=False)}")

    if budget:
        budget.check()
    comp = provider.chat(
        [{"role": "system", "content": _SUGGEST_SYS},
         {"role": "user", "content": user}],
        model=model, json_mode=True,
        max_tokens=budget.cap_tokens(900) if budget else 900)
    if budget:
        budget.record(comp.usage)

    data = comp.as_json()
    raw = data.get("suggestions") if isinstance(data, dict) else {}
    raw = raw if isinstance(raw, dict) else {}

    ok: dict[str, dict] = {}
    rejected: dict[str, str] = {}
    by_id = {i.id: i for i in missing}

    for pid, item in raw.items():
        idef = by_id.get(pid)
        if idef is None:
            rejected[pid] = "该参数不在待建议清单里，已丢弃"
            continue
        value = item.get("value") if isinstance(item, dict) else item
        # 关键闸门：与用户手输走同一条校验通道
        try:
            _coerce(idef, value, spec, know)
        except Exception as exc:
            rejected[pid] = f"建议值 {value!r} 没通过参数校验：{exc}"
            continue
        ok[pid] = {
            "value": value,
            "rationale": str(item.get("rationale", "")) if isinstance(item, dict) else "",
            "unit": idef.unit,
            "name_zh": idef.name_zh,
        }

    skipped = data.get("skipped") if isinstance(data, dict) else {}
    return {
        "suggestions": ok,
        "skipped": skipped if isinstance(skipped, dict) else {},
        "rejected": rejected,
        "source": "ai",
        "usage": comp.usage.to_dict(),
        "note": "以上为 AI 建议值，需逐项确认后才会写入参数表。",
    }


def _enum_choices_for(spec, know, idef) -> list[dict]:
    from mds.runner import enum_choices
    return enum_choices(spec, know).get(idef.id, [])


def _suggest_offline(spec, know, missing) -> dict:
    """离线兜底：只用规格里写明的 typical / default，不猜。"""
    out: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for i in missing:
        val = i.typical if i.typical is not None else i.default
        if val is None:
            skipped[i.id] = "规格里没有给推荐值，需要你按实际工况填写"
            continue
        out[i.id] = {
            "value": val,
            "rationale": "取自工作流规格中标注的典型值（离线模式，无 AI 参与）",
            "unit": i.unit,
            "name_zh": i.name_zh,
        }
    return {"suggestions": out, "skipped": skipped, "rejected": {},
            "source": "offline",
            "note": "离线模式：仅给出规格里写明的典型值，仍需你确认。"}


# --- 阶段 3：白话解释 -------------------------------------------------------

def explain(step: dict, context: dict, provider: Provider, model: str,
            budget=None) -> dict:
    """解释一步已经算完的计算。输入是 trace，输出是纯文本，不产生任何数值。"""
    payload = {
        "物料": context.get("name_zh", ""),
        "依据标准": context.get("standard", ""),
        "步骤": step.get("name_zh"),
        "公式": step.get("formula"),
        "代入": step.get("substitution"),
        "结果": f"{step.get('value_display')} {step.get('unit') or ''}".strip(),
        "信源": (step.get("source") or {}).get("ref", ""),
        "信源状态": (step.get("source") or {}).get("confidence", ""),
    }
    if budget:
        budget.check()
    comp: Completion = provider.chat(
        [{"role": "system", "content": _EXPLAIN_SYS},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        model=model, json_mode=False,
        max_tokens=budget.cap_tokens(500) if budget else 500)
    if budget:
        budget.record(comp.usage)
    return {"text": comp.text.strip(), "source": "ai",
            "usage": comp.usage.to_dict()}


# --- 新物料：起草工作流 -----------------------------------------------------

_DRAFT_SYS = """你是机械设计选型引擎的工作流起草模块。

知识库里没有用户要选的这种物料。你的任务是起草一份**可执行的选型工作流规格**，
交给确定性引擎执行。你不执行计算，只描述流程。

## 绝对禁止（违反任何一条，整份规格会被程序拒绝）

1. **不得使用 table_lookup / table_interp / table_pick / row_select / round_to_series**
   这五种步骤要查数据表，而这个物料没有数据表。
   只能用：formula、check、select、default、text、bucket。

2. **不得把"手册会列成表的量"写成公式里的数字。**
   工况系数、安全系数、许用应力、材料常数、标准系列值、经验系数——
   这些一律做成 inputs 里的一项，让工程师自己从手册查了填。
   公式里可以出现的数字只有：物理常数、几何关系中的定值（如 π、2、1/2）、
   单位换算因子（如 1000、60000、9550）。
   例：`tau = 8 * K * F * D / (pi * d ** 3)` 可以（K、F、D、d 都是输入）；
       `tau = 8 * 1.25 * F * D / (pi * d ** 3)` 不可以（1.25 是曲度系数，该由人填）。

3. **不得编造标准号。** 拿不准就把 standard 写成空字符串，
   或在 notes 里写"依据待用户确认"。宁可空着，也不要写一个看起来像真的编号。

## 必须做到

- 每个 input 都要有 name_zh、unit（无量纲留空）、domain（合理的取值范围）；
  凡是需要查手册的量，hint 里**必须写明去哪查**（哪本手册/标准的什么表）。
- 每个 formula 与 check 步骤都要有 source.ref，说明这个公式出自哪里。
  不确定就写"通用工程公式，待核"。
- 至少要有一项 check（校核），否则这不是选型，只是算术。
- notes 里第一条必须说明这份流程是 AI 起草的、哪些量需要用户自己查依据。

## 可用的表达式

函数：min max abs round int float floor ceil sqrt log exp ent sin cos tan
      asin acos atan radians degrees
常量：pi e
运算：+ - * / ** ( )，以及三元表达式 `a if cond else b`

## 步骤类型

- formula: {id, kind: formula, name_zh, expr, unit, source: {ref}, outputs: [变量名]}
- check:   {id, kind: check, name_zh, value: 变量名, op: "<=", limit: 变量名或数字字符串,
            unit, on_fail: "不通过时该怎么改", source: {ref}}
- select:  {id, kind: select, name_zh, from_input: 输入id, candidates: [{value, label}],
            reason: "为什么要人来定", outputs: [变量名]}
- default: {id, kind: default, name_zh, from_input: 输入id, fallback: "表达式", outputs: [变量名]}
- text:    {id, kind: text, name_zh, template: "带 {变量} 的模板", outputs: [变量名]}
- bucket:  {id, kind: bucket, name_zh, value: 变量名,
            bins: [{max: 数, key: 档名}, {key: 兜底档名}], outputs: [变量名]}

只输出 JSON，格式：
{"material": "英文小写下划线id", "name_zh": "中文名", "standard": "标准号或空",
 "notes": ["第一条说明这是AI起草的…"],
 "inputs": [...], "steps": [...],
 "result": [{"label": "项目名", "value": "{变量}", "unit": "单位"}],
 "procure": {"material_template": "generic", "channels": ["taobao"],
             "fields": {"kind": "物料名", "spec": "{变量}"}},
 "confidence_note": "这份流程哪里最不确定"}"""

# 起草的规格绝不能用这五种步骤 —— 它们都要查数据表，而新物料没有表。
# 这不是提示词约束，是下面 _audit_draft() 真的会拒。
_TABLE_KINDS = {"table_lookup", "table_interp", "table_pick",
                "row_select", "round_to_series"}


class DraftRejected(AIError):
    """起草的规格没过闸门。reasons 逐条说明哪里不合规，便于重试或人工修。"""

    def __init__(self, message: str, reasons: list[str]):
        super().__init__(message, kind="draft_rejected")
        self.reasons = reasons

    def as_dict(self) -> dict:
        d = super().as_dict()
        d["reasons"] = self.reasons
        return d


def _audit_draft(data: dict) -> list[str]:
    """闸门二：起草的规格不得携带数据。

    `spec.parse()` 管格式合法性（闸门一），这里只管一件事——
    **不准把手册里的数当成常量写进流程**。查表步骤一律拒绝，
    因为新物料没有任何数据表，它引用的表要么不存在、要么是模型编的。
    """
    bad: list[str] = []

    for raw in data.get("steps") or []:
        if not isinstance(raw, dict):
            continue
        sid = raw.get("id", "?")
        kind = raw.get("kind")
        if kind in _TABLE_KINDS:
            bad.append(f"步骤 {sid} 用了 {kind} —— 新物料没有数据表，"
                       f"需要查表的量请改成 inputs 里的一项，让用户自己填")
        if raw.get("table"):
            bad.append(f"步骤 {sid} 引用了数据表 {raw['table']!r}，但这个物料没有任何表")
        if kind in ("formula", "check") and not (raw.get("source") or {}).get("ref"):
            bad.append(f"步骤 {sid} 没有写 source.ref —— 每个公式都要说明出处")

    inputs = data.get("inputs") or []
    if not inputs:
        bad.append("没有任何输入项 —— 选型至少要问用户要工况")
    for raw in inputs:
        if isinstance(raw, dict) and not raw.get("name_zh"):
            bad.append(f"输入项 {raw.get('id', '?')} 没有中文名，界面上会只显示英文 id")

    if not any(isinstance(s, dict) and s.get("kind") == "check"
               for s in data.get("steps") or []):
        bad.append("没有任何校核步骤 —— 只有算术没有校核，那不是选型")

    if not data.get("result"):
        bad.append("没有 result —— 用户看不到最终选型结果")

    return bad


def draft_workflow(material_text: str, existing: list[str], provider: Provider,
                   model: str, budget=None) -> dict:
    """为知识库里没有的物料起草一份可执行工作流。

    两道闸门都过了才返回。返回的是**草稿**，还没有落盘——
    存不存、什么时候存，由用户在跑完一次完整选型之后决定。
    """
    text = (material_text or "").strip()
    if not text:
        raise AIError("没有说要选什么物料。", kind="empty")

    user = (f"用户要选的物料：{text}\n\n"
            f"引擎里已有的物料 id（不要与它们重名）：{', '.join(existing)}")

    if budget:
        budget.check()
    comp = provider.chat(
        [{"role": "system", "content": _DRAFT_SYS},
         {"role": "user", "content": user}],
        model=model, json_mode=True,
        max_tokens=budget.cap_tokens(3000) if budget else 3000)
    if budget:
        budget.record(comp.usage)

    data = comp.as_json()
    if not isinstance(data, dict):
        raise AIError("起草结果不是一个对象", kind="bad_json")

    # 闸门二：不得携带数据
    reasons = _audit_draft(data)
    if reasons:
        raise DraftRejected(
            f"AI 起草的流程没通过合规检查（{len(reasons)} 处）。"
            "这不是你的问题——引擎宁可拒绝，也不让一份带着编造系数的流程跑起来。",
            reasons)

    # 起草的规格一律打上出身与最低置信度，且不能与已有物料重名
    mid = re.sub(r"[^a-z0-9_]", "_", str(data.get("material") or "").lower()).strip("_")
    if not mid:
        raise AIError("起草结果没有给出合法的物料 id", kind="bad_draft")
    if mid in existing:
        mid = f"{mid}_user"
    data["material"] = mid
    data["provenance"] = "ai_generated"
    data["generated_by"] = model
    data["schema_version"] = 1

    notes = [str(n) for n in (data.get("notes") or [])]
    notes.insert(0, (
        f"**本流程由 AI（{model}）起草，未经任何核验。** "
        "引擎只保证它在格式与表达式上合法、且不携带任何编造的数据表——"
        "公式是否适用于你的工况、系数该取多少，需要你对照手册自行确认。"
        "凡是需要查依据的量都做成了输入项，hint 里写了去哪查。"))
    data["notes"] = notes

    # 闸门一：与随包工作流走完全同一个解析器与静态校验
    from mds import spec as mds_spec
    try:
        parsed = mds_spec.parse(data)
    except Exception as exc:
        raise DraftRejected(
            "AI 起草的流程没通过引擎的静态校验。",
            [f"{type(exc).__name__}: {exc}"]) from exc

    return {
        "material": parsed.material,
        "name_zh": parsed.name_zh or text,
        "spec": data,
        "inputs": len(parsed.inputs),
        "steps": len(parsed.steps),
        "checks": sum(1 for s in parsed.steps if s.kind == "check"),
        "confidence_note": str(data.get("confidence_note") or ""),
        "source": "ai",
        "usage": comp.usage.to_dict(),
    }

"""三个窄接口 —— AI 能做的全部事情。

| 接口              | 做什么                          | 未绑定 / 离线时            |
|-------------------|---------------------------------|----------------------------|
| `parse_intent`    | 自然语言 → 物料 + 已知参数      | 关键词匹配                 |
| `suggest_params`  | 缺失参数 → 建议值 + 理由        | 读规格里的 typical/default |
| `explain`         | 已算好的 StepTrace → 白话       | 关闭                       |

三条硬边界，都是结构性的，不是靠注释约束的：

1. **AI 不做任何算术。** 这里没有一处把模型输出当成计算结果。
2. **建议值必须过校验闸门。** 每个建议值都跑一遍 `mds.runner._coerce`——
   与用户手输完全同一条通道。过不了的直接标成 rejected 返回，
   调用方拿不到一个"绕过校验的数"。
3. **建议不会自动生效。** 返回的是 suggestion，写不写进参数由人决定。
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

只输出 JSON，格式：
{"material": "物料id或null", "values": {"参数id": 数值或字符串},
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
    if material not in allowed:
        material = None

    return {
        "material": material,
        "values": data.get("values") if isinstance(data.get("values"), dict) else {},
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
        "unmatched": [],
        "notes": "离线模式：仅按关键词粗匹配，请核对识别结果是否正确。",
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

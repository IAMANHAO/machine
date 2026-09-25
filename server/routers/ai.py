"""AI 的三个通用接口（intent / suggest / explain）。

都不碰数值计算。`suggest` 的每个建议值在返回前都走过与手输完全相同的
校验闸门（mds.runner._coerce），过不了的以 rejected 返回，调用方拿不到
一个绕过校验的数。

未绑定 / 离线时：intent 与 suggest 自动降级到本地规则，explain 返回 409
（没有 AI 就没有白话解释，这一项没有诚实的降级方案）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import ai, engine
from ..ai import tasks

router = APIRouter(tags=["ai"], prefix="/ai")


class IntentIn(BaseModel):
    text: str
    mode: str = "auto"


class SuggestIn(BaseModel):
    material: str
    known: dict = Field(default_factory=dict)
    mode: str = "auto"


class ExplainIn(BaseModel):
    material: str
    step: dict
    mode: str = "auto"


def _fail(exc: Exception, status: int = 400):
    detail = exc.as_dict() if hasattr(exc, "as_dict") else {
        "error": type(exc).__name__, "message": str(exc)}
    return HTTPException(status_code=status, detail=detail)


@router.post("/intent")
def intent(body: IntentIn) -> dict:
    """阶段 0：自然语言 → 物料 + 已知参数。离线退化为关键词匹配。"""
    provider = ai.current_provider(body.mode)
    try:
        return tasks.parse_intent(body.text, engine.materials(), provider,
                                  ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.post("/suggest")
def suggest(body: SuggestIn) -> dict:
    """阶段 2：为缺失参数给建议值。返回的是建议，写不写由人定。"""
    try:
        spec = engine.workflow_spec(body.material)
    except engine.MDSError as exc:
        raise _fail(exc, 404)

    provider = ai.current_provider(body.mode)
    try:
        return tasks.suggest_params(spec, engine.knowledge(), body.known,
                                    provider, ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.post("/explain")
def explain(body: ExplainIn) -> dict:
    """阶段 3：解释某一步。输入是已算好的 trace，输出纯文本。"""
    provider = ai.current_provider(body.mode)
    if provider is None:
        raise _fail(ai.AIError(
            "解释功能需要绑定账号。计算、校核、出表、采购链接不受影响，照常可用。",
            kind="not_bound"), 409)
    try:
        spec = engine.workflow_spec(body.material)
    except engine.MDSError as exc:
        raise _fail(exc, 404)

    context = {"name_zh": spec.name_zh, "standard": spec.standard}
    try:
        return tasks.explain(body.step, context, provider,
                             ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


# --- 知识库里没有的物料：起草 + 保存 ----------------------------------------

# 「AI 一次性起草」的两个接口（/draft-workflow、/save-draft）已删除：
# 门槛太低——能拦住编造的数据表，拦不住编造的公式。取而代之的是
# /api/guided/*，那是一条有检索、有取证、有用户确认的路（见 docs/decisions.md）。
# 删除已保存物料的接口留在这里，因为它对两种出身的物料都适用。


@router.delete("/saved/{material}")
def delete_saved(material: str) -> dict:
    """删掉一个已保存的自建物料。随包物料不在这个目录里，删不掉。"""
    from .. import drafts

    try:
        out = drafts.remove(material)
    except drafts.DraftError as exc:
        raise _fail(exc, 404)
    engine.reset_caches()
    return out


# --- 参数补齐与叫法对齐：让流程别卡住 ---------------------------------------
#
# 三条接口来自同一个要求：**不要把用户堵死在一个报错上**。
# 但"不堵死"不等于"随便放个数进去"——每一个由 AI 填进来的值都要留下出身，
# 调用方拿到的 filled 里带着 rationale，界面据此打标，trace 警告里也会列出来。

class FillIn(BaseModel):
    material: str = ""
    session: str = Field(default="", description="引导式会话 id（规格还没落盘时用）")
    known: dict = Field(default_factory=dict)
    reply: str = Field(default="", description="用户对上一轮 questions 的回答")
    mode: str = "auto"


class AlignIn(BaseModel):
    label: str
    value: str
    candidates: list = Field(default_factory=list)
    mode: str = "auto"


class FixIn(BaseModel):
    material: str = ""
    session: str = ""
    param: str
    value: object = None
    problem: str = ""
    known: dict = Field(default_factory=dict)
    mode: str = "auto"


def _resolve_spec(material: str, session: str):
    """拿到规格对象。引导式的规格还没落盘，只能从会话里组装。"""
    if session:
        from mds import spec as mds_spec

        from .. import guided
        try:
            sess = guided.load(session)
            return mds_spec.parse(guided.spec_dict(sess))
        except guided.GuidedError as exc:
            raise _fail(exc, 409)
    if not material:
        raise _fail(ValueError("要么给 material，要么给 session"), 422)
    try:
        return engine.workflow_spec(material)
    except engine.SpecError as exc:
        raise _fail(exc, 404)


def _need(mode: str, what: str):
    provider = ai.current_provider(mode)
    if provider is None:
        raise _fail(ai.AIError(
            f"{what}需要绑定账号并联网。离线时请按提示自己填——"
            "计算、校核、出表全部照常可用。", kind="not_bound"), 409)
    return provider


@router.post("/fill-params")
def fill_params(body: FillIn) -> dict:
    """参数没填完就想算：**能负责任地补的补上，补不了的问出来**。

    每个补上的值都过了与手输相同的校验闸门，并且必须带着理由——
    说不出为什么的数不配直接写进参数表。调用方要把这些 id 标成 AI 补的。
    """
    provider = _need(body.mode, "参数补齐")
    spec = _resolve_spec(body.material, body.session)
    try:
        return tasks.fill_params(spec, engine.knowledge(), body.known,
                                 provider, ai.current_model(),
                                 reply=body.reply, budget=ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.post("/align")
def align(body: AlignIn) -> dict:
    """用户给的叫法 → 候选里的哪一个。对不上就返回 null，由界面照常让人重选。

    **返回的 value 必然在候选里**：模型不可能从这里造出一个新选项。
    """
    provider = _need(body.mode, "叫法对齐")
    try:
        return tasks.align_choice(body.label, body.value, body.candidates,
                                  provider, ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.post("/advise-fix")
def advise_fix(body: FixIn) -> dict:
    """某个参数没过校验时给一个改法，而不是把人堵在报错上。

    建议值照样要过校验闸门——它是建议，不是特权。
    """
    provider = _need(body.mode, "参数修改建议")
    spec = _resolve_spec(body.material, body.session)
    try:
        return tasks.advise_fix(spec, engine.knowledge(), body.param, body.value,
                                body.problem, body.known, provider,
                                ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)

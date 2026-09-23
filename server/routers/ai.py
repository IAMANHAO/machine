"""AI 三接口。

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

class DraftIn(BaseModel):
    material_text: str = Field(description="用户说的物料名，如「磁吸铁片」")
    mode: str = "auto"


class SaveDraftIn(BaseModel):
    spec: dict = Field(description="draft 返回的那份 spec，原样回传")


@router.post("/draft-workflow")
def draft_workflow(body: DraftIn) -> dict:
    """知识库里没有的物料 → AI 起草一份可执行的选型流程。

    **这是在线增强，没有离线降级方案**：离线时引擎没有任何依据能凭空造出
    一个物料的选型流程，只能如实说不行。

    起草结果要过两道闸门（格式合法 + 不携带数据表）才会返回，
    且返回的是**草稿**，还没落盘——跑通一次完整选型之后才由用户决定存不存。
    """
    provider = ai.current_provider(body.mode)
    if provider is None:
        raise _fail(ai.AIError(
            "起草新物料的选型流程需要绑定账号并联网。"
            "已有的 12 个物料离线照常可用；"
            "你也可以按 docs/workflow-spec.md 自己写一份 YAML 放进用户目录。",
            kind="not_bound"), 409)
    try:
        return tasks.draft_workflow(
            body.material_text, engine.material_ids(),
            provider, ai.current_model(), ai.budget())
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except tasks.DraftRejected as exc:
        # 422：草稿本身不合规，不是服务出错。reasons 逐条告诉前端哪里不行。
        raise _fail(exc, 422)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.post("/save-draft")
def save_draft(body: SaveDraftIn) -> dict:
    """把跑通过的草稿存进用户目录，**下次离线也能选这个物料**。

    落盘前会再独立校验一遍——不信任调用方，也不信任草稿在内存里待过一段时间。
    """
    from .. import drafts

    try:
        out = drafts.save(body.spec)
    except drafts.DraftError as exc:
        raise _fail(exc, 422)
    engine.reset_caches()          # 让物料列表立刻看到它
    return out


@router.delete("/saved/{material}")
def delete_saved(material: str) -> dict:
    """删掉一个已保存的生成物料。随包物料不在这个目录里，删不掉。"""
    from .. import drafts

    try:
        out = drafts.remove(material)
    except drafts.DraftError as exc:
        raise _fail(exc, 404)
    engine.reset_caches()
    return out

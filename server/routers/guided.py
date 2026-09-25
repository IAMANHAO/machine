"""引导式选型的接口 —— 按 SKILL.md 的阶段一段一段推进。

## 闸门在这边，不在前端

前端会根据 `can_*` 把按钮置灰，但那只是提示。**真正拦住的是这里的 409**：

- 依据还没确认就调 `/inputs` → 409
- 参数还没确认就调 `/steps` → 409
- 整套公式还没确认就想拿规格去跑 → 409

把闸门放在前端等于没有闸门——curl 一下就绕过去了。

## 没有离线降级方案

阶段 1 要真的联网检索并取证依据。离线或未绑定账号时这条路直接关闭，
如实说明原因，而不是给一个点了没反应的按钮。已有的 12 个物料离线照常可用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import ai, engine, guided

router = APIRouter(tags=["guided"], prefix="/guided")


class StartIn(BaseModel):
    material_text: str = Field(description="用户说的物料名，如「磁吸铁片」")
    mode: str = "auto"


class BasisIn(BaseModel):
    basis_id: str = ""
    custom: dict | None = Field(
        default=None,
        description="用户自己填的依据 {claim, standard, outline, urls}")


class InputsIn(BaseModel):
    inputs: list | None = Field(
        default=None, description="用户改过的参数清单；不传表示照单全收")


class ConfirmIn(BaseModel):
    confirmed: bool = Field(
        default=False,
        description="勾选「我已对照依据核对以上全部公式」")


def _fail(exc: Exception, status: int = 400):
    detail = exc.as_dict() if hasattr(exc, "as_dict") else {
        "error": type(exc).__name__, "message": str(exc)}
    return HTTPException(status_code=status, detail=detail)


# GuidedError 的 kind → HTTP 状态。顺序不对是 409（流程状态不允许），
# 不是 400（请求本身没毛病）——这个区分让前端能给出正确的提示。
_STATUS = {
    "not_found": 404,
    "empty": 400,
    "bad_basis": 400,
    "bad_inputs": 400,
    "unknown_basis": 404,
    "unverified_basis": 422,
    "no_search_results": 422,
    "parse_failed": 422,
}


def _guided_fail(exc: guided.GuidedError):
    return _fail(exc, _STATUS.get(exc.kind, 409))


def _need_provider(mode: str):
    provider = ai.current_provider(mode)
    if provider is None:
        raise _fail(ai.AIError(
            "引导式选型要联网：阶段 1 得真的检索并取证依据，"
            "阶段 2、3 要由模型按那份依据给出参数清单与计算步骤。"
            "请先在设置页绑定 AI 账号并联网。已有的 12 个物料离线照常可用。",
            kind="not_bound"), 409)
    return provider


def _run(fn, *args, **kwargs):
    """统一的异常翻译。四类分开处理，不要笼统地报 500。"""
    from ..ai import tasks

    try:
        return fn(*args, **kwargs)
    except guided.GuidedError as exc:
        raise _guided_fail(exc)
    except ai.BudgetExceeded as exc:
        raise _fail(exc, 429)
    except tasks.GuidanceRejected as exc:
        # 422：模型连着几轮都没给出合规输出。reasons 与 repair_log 都带回去，
        # 用户有权知道它卡在哪、修了几次。
        raise _fail(exc, 422)
    except ai.AIError as exc:
        raise _fail(exc, 502 if exc.retryable else 400)


@router.get("")
def list_sessions(limit: int = 10) -> dict:
    """没走完的引导会话。用户中途去翻手册、隔天回来要能接着走。"""
    return {"sessions": guided.listing(limit)}


@router.post("")
def start(body: StartIn) -> dict:
    """阶段 0：开一个会话。此时还没有检索，也还没花一个 token。"""
    _need_provider(body.mode)
    return guided.view(_run(guided.start, body.material_text, ai.current_model()))


@router.get("/{sid}")
def get_session(sid: str) -> dict:
    return guided.view(_run(guided.load, sid))


@router.delete("/{sid}")
def discard(sid: str) -> dict:
    return guided.discard(sid)


@router.post("/{sid}/research")
def research(sid: str, mode: str = "auto") -> dict:
    """阶段 1：检索 → AI 挑候选依据 → **服务端逐条取证**。

    取证不通过的候选会照常返回（界面要如实显示它为什么不行），
    但 `choose_basis` 选不了它。
    """
    from ..ai import websearch

    provider = _need_provider(mode)
    # 降级阶梯的第 ② 级：没绑搜索服务时，看这家 AI 自己会不会联网。
    # 绑了 DeepSeek 的人不必再绑第二把 key —— 它的联网在 Anthropic 兼容端点上。
    sess = _run(guided.research, sid, provider, ai.current_model(), ai.budget(),
                websearch.current_searcher())
    return guided.view(sess)


@router.post("/{sid}/basis")
def choose_basis(sid: str, body: BasisIn) -> dict:
    """用户拍板选一条依据（或自己填）。**这一步之前什么都不许往下走。**"""
    return guided.view(_run(guided.choose_basis, sid, body.basis_id, body.custom))


@router.post("/{sid}/inputs")
def propose_inputs(sid: str, mode: str = "auto") -> dict:
    """阶段 2：按已确认的依据列出要问用户的参数（分轮，单轮 ≤6 项）。"""
    provider = _need_provider(mode)
    return guided.view(_run(guided.propose_inputs, sid, provider,
                            ai.current_model(), ai.budget()))


@router.post("/{sid}/inputs/confirm")
def confirm_inputs(sid: str, body: InputsIn) -> dict:
    return guided.view(_run(guided.confirm_inputs, sid, body.inputs))


@router.post("/{sid}/steps")
def propose_steps(sid: str, mode: str = "auto") -> dict:
    """阶段 3/4：整套计算与校核。给完还不能跑，要等用户确认。"""
    provider = _need_provider(mode)
    return guided.view(_run(guided.propose_steps, sid, provider,
                            ai.current_model(), ai.budget()))


@router.post("/{sid}/steps/confirm")
def confirm_formulas(sid: str, body: ConfirmIn) -> dict:
    """用户一次性过目并确认整套公式。**没有这一步，引擎不执行。**"""
    return guided.view(_run(guided.confirm_formulas, sid, body.confirmed))


@router.post("/{sid}/ai-draft")
def ai_draft(sid: str, mode: str = "auto") -> dict:
    """兜底档：让 AI 直接把整个选型做完，**包括出数**。

    **这条返回的不是选型结果。** 它没有经过确定性引擎，里面每个数都没有出处；
    它不会写进会话的 steps/result，所以既变不成物料，也进不了选型报告。
    引擎唯一做的事是重算了 AI 自己写的代入式，看它有没有算错——
    算术对得上不等于公式适用，这两件事分开报。

    前面几道闸门都过不去时，总得有东西交给用户；代价写在返回的文本头里。
    """
    provider = _need_provider(mode)
    sess = _run(guided.ai_draft, sid, provider, ai.current_model(), ai.budget())
    return {"draft": sess.ai_draft, "session": guided.view(sess)}


@router.get("/{sid}/spec")
def spec(sid: str) -> dict:
    """组装好的规格。公式没确认就 409 —— 拿不到也就跑不了。"""
    sess = _run(guided.load, sid)
    return _run(guided.spec_dict, sess)


class RunIn(BaseModel):
    values: dict = Field(default_factory=dict)
    choices: dict = Field(default_factory=dict)
    # 与 /api/selection/run 一样：引擎不认识这两个字段，它们只用来
    # 在结果里如实标出哪些值是 AI 补的、哪些是 AI 对齐叫法后改的。
    ai_filled: dict = Field(default_factory=dict)
    ai_aligned: dict = Field(default_factory=dict)


@router.get("/{sid}/workflow")
def workflow(sid: str) -> dict:
    """把已确认的规格画成阶段 2 的表单。

    与 `/api/workflow/{material}` 形状完全相同——引导出来的物料在工作台里
    与随包物料没有任何区别，前端不需要为它写第二套渲染。
    """
    from mds import spec as mds_spec

    sess = _run(guided.load, sid)
    data = _run(guided.spec_dict, sess)
    return engine.payload_of(mds_spec.parse(data))


@router.post("/{sid}/run")
def run(sid: str, body: RunIn) -> dict:
    """用会话里已确认的规格跑一次选型。**落盘之前也能跑。**

    这是"跑通了才存"的前提：一份没跑通的流程存下来，只会在物料列表里
    留一个点进去就报错的入口。走的是同一个 `runner.run`。
    """
    from mds import spec as mds_spec

    sess = _run(guided.load, sid)
    data = _run(guided.spec_dict, sess)
    try:
        out = engine.execute_spec(mds_spec.parse(data), body.values, body.choices)
        engine.note_ai_origins(out["trace"], body.ai_filled, body.ai_aligned)
        return out
    except engine.MDSError as exc:
        raise _fail(exc, 400)


@router.post("/{sid}/save")
def save(sid: str) -> dict:
    """存进用户目录，**下次离线也能选这个物料**。

    落盘前 `drafts.save()` 会用同一份判据再独立复核一遍——
    不信任调用方，也不信任这份规格在会话里待过的那二十分钟。
    """
    from .. import drafts

    try:
        out = _run(guided.save, sid)
    except HTTPException:
        raise
    except drafts.DraftError as exc:
        raise _fail(exc, 422)
    engine.reset_caches()          # 让物料列表立刻看到它
    return out

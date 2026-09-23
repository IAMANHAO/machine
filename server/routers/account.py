"""账号绑定 —— BYOK，支持多家服务商。

DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI，以及任何自填
base_url 的 OpenAI 兼容服务。**四家都只提供 API Key + Bearer 认证，
没有面向第三方应用的 OAuth 授权登录**，所以不存在真正的"跳转登录"。
这里把它做成一条引导式绑定流程：
选服务商 → 说明费用归属 → 打开其控制台 → 粘回 key → 最小验证 → 显示已绑定。

可以同时绑定多家、其中一家生效，用 POST /account/activate 切换。
这不是花哨：一家余额用完时能立刻切到另一家，比"解绑再重绑"实用——
解绑会把 key 从系统凭据库删掉，用户还得回控制台重新复制一遍。

任何响应都不回显 key 本身，只给脱敏标签。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import ai

router = APIRouter(tags=["account"], prefix="/account")

# 兼容旧前端：没指定服务商时仍按 DeepSeek 的控制台给入口
CONSOLE_URL = ai.provider_spec(ai.DEFAULT_PROVIDER).console_url


class BindIn(BaseModel):
    api_key: str = Field(description="用户自己在服务商控制台创建的 API Key")
    provider: str = ai.DEFAULT_PROVIDER
    base_url: str = ""          # 留空就用该服务商的默认 base_url
    model: str = ""             # 没有模型列表接口的服务商（方舟/百炼）必须填


class ModelIn(BaseModel):
    model: str


class ActivateIn(BaseModel):
    provider: str


class LimitsIn(BaseModel):
    max_tokens_per_call: int = Field(ge=100, le=8000)
    max_calls_per_day: int = Field(ge=1, le=10000)
    enabled: bool = True


def _fail(exc: Exception, status: int = 400):
    detail = exc.as_dict() if hasattr(exc, "as_dict") else {
        "error": type(exc).__name__, "message": str(exc)}
    return HTTPException(status_code=status, detail=detail)


@router.get("")
def get_status(mode: str = "auto") -> dict:
    body = ai.status(mode)
    body["console_url"] = CONSOLE_URL
    body["notice"] = (
        "本软件不自带 API key，也不自建中转服务。AI 由你绑定自己的账号提供，"
        "调用费用直接计入你的账号；请求由本机直连服务商，工况参数不经过第三方。"
        "未绑定不影响使用：计算、校核、出表、采购链接全部走本地确定性内核。")
    return body


@router.get("/providers")
def list_providers() -> dict:
    """可绑定的服务商清单。**只有接入事实，不含任何凭据。**"""
    return {"providers": ai.provider_catalog(), "default": ai.DEFAULT_PROVIDER}


@router.post("/bind")
def bind(body: BindIn) -> dict:
    try:
        return ai.bind(body.api_key, provider=body.provider,
                       base_url=body.base_url, model=body.model)
    except ai.AIError as exc:
        # 401 等上游状态原样透出去，让前端能给出准确提示
        bad_request = ("unauthorized", "no_models", "model_required",
                       "base_url_required")
        raise _fail(exc, 422 if exc.kind in bad_request else 400)
    except ai.CredentialError as exc:
        raise _fail(exc, 500)


@router.post("/activate")
def activate(body: ActivateIn) -> dict:
    """切换当前生效的账号。不动任何凭据——切回来还是原来那把 key。"""
    try:
        return ai.activate(body.provider)
    except ai.AIError as exc:
        raise _fail(exc, 409)


@router.delete("")
def unbind(provider: str | None = None) -> dict:
    """解绑。不指定 provider 就解绑当前生效的那家。"""
    return ai.unbind(provider)


@router.get("/models")
def models() -> dict:
    provider = ai.current_provider("online")
    if provider is None:
        raise _fail(ai.AIError("尚未绑定账号。", kind="not_bound"), 409)
    try:
        models = provider.list_models()
    except ai.AIError as exc:
        raise _fail(exc)
    return {
        "models": models,
        "current": ai.current_model(),
        "provider": provider.spec.to_dict(),
        "note": ("" if models else
                 f"{provider.spec.name_zh}没有提供模型列表接口，"
                 f"模型名需要你从其控制台复制填入。"),
    }


@router.post("/model")
def set_model(body: ModelIn) -> dict:
    from ..config import data_dir

    b = ai.binding()
    if b is None:
        raise _fail(ai.AIError("尚未绑定账号。", kind="not_bound"), 409)
    # 只有真拿到过模型列表时才用它拦人。方舟/百炼没有这个接口，
    # models_seen 里只有绑定时验过的那一个——拿它当白名单会把用户
    # 锁死在第一次填的模型上。
    spec = ai.provider_spec(b.provider)
    if spec.may_list_models and b.models_seen and body.model not in b.models_seen:
        raise _fail(ai.AIError(
            f"{body.model!r} 不在该账号可用的模型清单里：{', '.join(b.models_seen)}",
            kind="unknown_model"), 422)
    b.model = body.model
    ai.save_binding(data_dir(), b)
    return {"binding": b.to_dict()}


@router.get("/balance")
def balance() -> dict:
    """真实余额。刻意不做"预估费用"——单价会变，硬编码价目表迟早给出过期数字。"""
    provider = ai.current_provider("online")
    if provider is None:
        raise _fail(ai.AIError("尚未绑定账号。", kind="not_bound"), 409)
    data = provider.balance()
    if data is None:
        who = provider.spec.name_zh
        note = (f"{who}没有提供面向 API key 的余额查询接口，请到其控制台查看。"
                if not provider.spec.balance_path
                else f"{who}的余额接口这次没有返回数据，请到其控制台查看。")
        return {"available": False, "note": note,
                "console_url": provider.spec.console_url}
    return {"available": True, **data}


@router.get("/limits")
def get_limits() -> dict:
    bd = ai.budget()
    return {"limits": bd.limits().to_dict(), "usage_today": bd.today()}


@router.put("/limits")
def set_limits(body: LimitsIn) -> dict:
    bd = ai.budget()
    limits = bd.set_limits(ai.Limits(**body.model_dump()))
    return {"limits": limits.to_dict(), "usage_today": bd.today()}

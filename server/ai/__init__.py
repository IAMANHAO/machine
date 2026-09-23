"""AI 层门面 —— 绑定状态、provider 构造、模式判定。

本软件**不自带任何 API key，不自建中转服务，不代付任何费用**。
AI 由用户绑定自己的账号提供，请求由本机直连服务商，工况参数不经过第三方。

未绑定账号时整个软件仍然完整可用：计算、校核、出表、采购链接全部走
本地确定性内核，与 AI 无关。AI 是可选增强，不是运行前提。

## 可以同时绑定多家

DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI，以及任何自填
base_url 的 OpenAI 兼容服务。每家一份凭据（系统凭据库里各占一个 profile），
其中一家是当前生效的。

**这不是为了花哨**：DeepSeek 余额用完时能立刻切到百炼继续干活，
比"解绑再重新绑定"实用得多——而且解绑会把 key 从凭据库删掉，
用户还得回控制台重新复制一遍。

各家的接入差异（模型列表、余额查询、关闭思考模式）登记在 `providers.py`。
"""

from __future__ import annotations

from ..config import data_dir
from .budget import Budget, BudgetExceeded, Limits
from .client import AIError, DEFAULT_BASE_URL, Provider, ValidationResult
from .credentials import (
    Account, Binding, CredentialError, DEFAULT_PROFILE, clear_binding,
    delete_key, load_account, load_binding, mask, now_iso, read_key,
    save_account, save_binding,
)
from .providers import DEFAULT_PROVIDER, REGISTRY, get as provider_spec, listing

__all__ = [
    "AIError", "Account", "Binding", "Budget", "BudgetExceeded",
    "CredentialError", "Limits", "Provider", "ValidationResult",
    "account", "activate", "bind", "binding", "budget", "current_provider",
    "effective_mode", "is_bound", "mask", "provider_catalog", "provider_spec",
    "status", "unbind", "DEFAULT_BASE_URL", "DEFAULT_PROVIDER",
]

# 用户偏好的运行模式；离线时一律不发请求
VALID_MODES = ("auto", "online", "offline")


def budget() -> Budget:
    return Budget(data_dir())


def provider_catalog() -> list[dict]:
    """可绑定的服务商清单。**只有接入事实，不含任何凭据。**

    刻意不叫 providers()——那会把同名的 providers 子模块遮住，
    `from server.ai import providers` 拿到的会是这个函数而不是模块。
    """
    return listing()


def account() -> Account:
    return load_account(data_dir())


def binding(provider: str | None = None) -> Binding | None:
    """某一家的绑定；不指定就是当前生效的那家。"""
    return account().get(provider)


def is_bound(provider: str | None = None) -> bool:
    """绑定 = 既有元数据也有 key。少一样都不算。"""
    b = binding(provider)
    return b is not None and bool(read_key(b.profile))


def effective_mode(preferred: str = "auto") -> str:
    """把用户偏好与真实能力合成最终模式。

    只有"已绑定 且 用户没强制离线"才会真的发请求。
    """
    if preferred not in VALID_MODES:
        preferred = "auto"
    if preferred == "offline":
        return "offline"
    return "online" if is_bound() else "offline"


def current_provider(preferred_mode: str = "auto") -> Provider | None:
    """拿到可用的 provider；离线或未绑定返回 None（调用方据此降级）。"""
    if effective_mode(preferred_mode) != "online":
        return None
    b = binding()
    if not b:
        return None
    key = read_key(b.profile)
    if not key:
        return None
    return Provider(key, spec=provider_spec(b.provider), base_url=b.base_url)


def current_model(override: str | None = None) -> str:
    b = binding()
    return (override or (b.model if b else "") or "").strip()


def bind(api_key: str, *, provider: str = DEFAULT_PROVIDER, base_url: str = "",
         model: str = "") -> dict:
    """验证并保存一家的绑定，绑完即生效。

    验证优先走**不花钱**的模型列表接口；方舟与百炼没有这个接口，
    会退回一次最小对话探针——那要花几个 token，结果里的 `cost_hint`
    会如实说出来。悄悄花掉用户的钱，哪怕只有几分，也不该做。
    """
    spec = provider_spec(provider)
    url = (base_url or spec.base_url).strip()
    if not url:
        raise AIError(f"{spec.name_zh}需要你填写 base_url。", kind="base_url_required")

    probe = Provider(api_key, spec=spec, base_url=url)
    result = probe.validate(model)

    from .credentials import store_key

    store_key(api_key, spec.id)
    b = Binding(
        profile=spec.id,
        provider=spec.id,
        base_url=url.rstrip("/"),
        model=result.model,
        label=mask(api_key),
        bound_at=now_iso(),
        models_seen=result.models,
    )
    save_binding(data_dir(), b)
    return {"binding": b.to_dict(), "models": result.models,
            "validation": result.to_dict(), "balance": probe.balance()}


def activate(provider: str) -> dict:
    """切换当前生效的账号。不动任何凭据——切回来还是原来那把 key。"""
    acc = account()
    if provider not in acc.bindings:
        raise AIError(f"没有绑定过 {provider_spec(provider).name_zh}。",
                      kind="not_bound")
    if not read_key(provider):
        raise AIError(
            f"{provider_spec(provider).name_zh}的元数据还在，但系统凭据库里找不到 key —— "
            "可能被其它程序清理过，请重新绑定。", kind="key_missing")
    acc.active = provider
    save_account(data_dir(), acc)
    return {"active": provider, "binding": acc.bindings[provider].to_dict()}


def unbind(provider: str | None = None) -> dict:
    """解绑一家（不指定就是当前生效那家），删掉它的 key 与元数据。"""
    acc = account()
    target = provider or acc.active
    if not target:
        return {"unbound": False, "key_removed": False, "reason": "本来就没有绑定"}
    removed = delete_key(target)
    clear_binding(data_dir(), target)
    return {"unbound": True, "provider": target, "key_removed": removed,
            "active": load_account(data_dir()).active}


def status(preferred_mode: str = "auto") -> dict:
    """给设置页与顶栏徽章用的完整状态。绝不回显 key 本身。"""
    acc = account()
    b = acc.get()
    bd = budget()
    spec = provider_spec(b.provider) if b else None
    return {
        "bound": is_bound(),
        "mode": preferred_mode,
        "effective_mode": effective_mode(preferred_mode),
        "binding": b.to_dict() if b else None,
        "provider": spec.to_dict() if spec else None,
        "active": acc.active,
        # 列出全部已绑定的账号，供设置页一键切换
        "bindings": [
            {**bd_.to_dict(),
             "name_zh": provider_spec(bd_.provider).name_zh,
             "active": pid == acc.active,
             "key_present": bool(read_key(pid))}
            for pid, bd_ in acc.bindings.items()
        ],
        "providers": provider_catalog(),
        "limits": bd.limits().to_dict(),
        "usage_today": bd.today(),
        "keyring_available": _keyring_ok(),
    }


def _keyring_ok() -> bool:
    from .credentials import HAS_KEYRING
    return HAS_KEYRING

"""Provider 客户端 —— OpenAI 兼容协议。

支持 DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI，以及任何
自填 base_url 的 OpenAI 兼容服务。各家的接入事实登记在 `providers.py`，
本模块只管按表行事：base_url 与厂商特有字段走 spec，api_key 走系统凭据库。

## 能力是探测出来的，不是声明死的

四家的"兼容"程度不一样：DeepSeek 有模型列表和余额查询；OpenAI 有模型列表
没有余额；方舟与百炼的官方文档都只演示了 chat.completions，没提模型列表。

但**把"某家不支持模型列表"写死在代码里，迟早会变成一条过期的断言**。
所以 `validate()` 真的去请求 `/models`，404/405 才退回最小对话探针。
spec 里的 `may_list_models` 只决定要不要先试，不决定结论。

## 绑定验证的代价必须说清楚

- 有模型列表的（DeepSeek / OpenAI）：`GET /models` **不消耗 token**
- 没有的（方舟 / 百炼）：发一次 `max_tokens=1` 的对话探针，**消耗几个 token**

这个区别会原样告诉用户（`ValidationResult.cost_hint`）。
悄悄花掉用户的钱，哪怕只有几分，也是这个产品不该做的事。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .credentials import mask
from .providers import DEFAULT_PROVIDER, ProviderSpec, get as get_spec

DEFAULT_BASE_URL = get_spec(DEFAULT_PROVIDER).base_url
DEFAULT_TIMEOUT = 45.0


class AIError(Exception):
    """AI 调用失败。message 是给用户看的话，已脱敏。"""

    def __init__(self, message: str, *, status: int | None = None,
                 kind: str = "ai_error", retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"error": self.kind, "message": str(self),
                "status": self.status, "retryable": self.retryable}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    model: str = ""

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "model": self.model,
        }


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)

    def as_json(self) -> Any:
        """把模型返回的 JSON 文本解析出来。解析不了就报错，不猜。"""
        raw = self.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIError(f"模型没有返回合法的 JSON：{raw[:120]}", kind="bad_json") from exc


@dataclass
class ValidationResult:
    """绑定验证的结果。cost_hint 要如实告诉用户这次验证花没花钱。"""

    models: list[str]
    model: str
    method: str          # "models_list" | "chat_probe"
    cost_hint: str
    usage: Usage | None = None

    def to_dict(self) -> dict:
        return {"models": self.models, "model": self.model,
                "method": self.method, "cost_hint": self.cost_hint,
                "usage": self.usage.to_dict() if self.usage else None}


class Provider:
    """一个 OpenAI 兼容服务的最小客户端。"""

    def __init__(self, api_key: str, *, spec: ProviderSpec | None = None,
                 base_url: str = "", timeout: float = DEFAULT_TIMEOUT):
        if not api_key or not api_key.strip():
            raise AIError("没有可用的 API key —— 请先在设置页绑定账号。",
                          kind="not_bound")
        self._key = api_key.strip()
        self.spec = spec or get_spec(DEFAULT_PROVIDER)
        # 自填服务没有内置 base_url，必须由调用方给
        self.base_url = (base_url or self.spec.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def __repr__(self) -> str:  # pragma: no cover - 防止 key 出现在堆栈里
        return f"<Provider {self.spec.id} {self.base_url} key={mask(self._key)}>"

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json"}

    def _friendly(self, status: int, body: str) -> AIError:
        """把 HTTP 错误翻成人话，并**带上是哪一家**。

        早先这里写死了"DeepSeek 账号"，接了四家之后那句话会指错地方——
        让用户跑去 DeepSeek 充值，而余额不足的其实是百炼。
        """
        who = self.spec.name_zh
        hint = {
            401: f"API key 无效或已被撤销。请在设置页重新绑定{who}账号。",
            402: f"账号余额不足。费用计入你自己的{who}账号，请前往其控制台充值。",
            403: f"这个 key 没有访问该模型的权限（{who}）。部分平台需要先在控制台开通模型。",
            404: f"{who}没有这个接口或模型。请核对 base_url 与模型名。",
            429: "调用过于频繁或超出配额，稍后再试。",
        }.get(status)
        if hint:
            kind = {401: "unauthorized", 402: "insufficient_balance",
                    403: "forbidden", 404: "not_found",
                    429: "rate_limited"}[status]
            return AIError(hint, status=status, kind=kind, retryable=status == 429)
        if 500 <= status < 600:
            return AIError(f"{who}服务端暂时不可用（{status}），稍后再试。",
                           status=status, kind="upstream_error", retryable=True)
        return AIError(f"调用{who}失败（HTTP {status}）：{body[:160]}", status=status)

    def _get(self, path: str) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{self.base_url}{path}", headers=self._headers)
        except httpx.RequestError as exc:
            raise AIError(f"连不上 {self.base_url}：网络不可达或被拦截。",
                          kind="network", retryable=True) from exc
        if r.status_code != 200:
            raise self._friendly(r.status_code, r.text)
        return r.json()

    # --- 能力探测 ---
    def list_models(self) -> list[str]:
        """GET /models。不支持的服务商返回空列表，**不当成错误**。

        方舟与百炼的官方文档都没提这个接口。但与其把"它们不支持"写死，
        不如真的去问一次——接口会变，写死的断言不会。
        """
        try:
            data = self._get("/models")
        except AIError as exc:
            if exc.status in (404, 405, 501):
                return []
            raise
        return [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]

    def balance(self) -> dict | None:
        """余额查询。只有声明了 balance_path 的服务商才有；失败不算致命。"""
        if not self.spec.balance_path:
            return None
        try:
            data = self._get(self.spec.balance_path)
        except AIError:
            return None
        infos = data.get("balance_infos") or []
        first = infos[0] if infos else {}
        return {
            "is_available": bool(data.get("is_available")),
            "currency": first.get("currency", ""),
            "total_balance": first.get("total_balance", ""),
            "granted_balance": first.get("granted_balance", ""),
            "topped_up_balance": first.get("topped_up_balance", ""),
        }

    # --- 绑定验证 ---
    def validate(self, model: str = "") -> ValidationResult:
        """验证 key 可用，并确定一个能用的模型名。

        优先走不花钱的模型列表；拿不到再退回最小对话探针，
        并**如实告诉用户这次验证消耗了 token**。
        """
        models = self.list_models() if self.spec.may_list_models else []
        if models:
            chosen = model if model in models else (model or models[0])
            return ValidationResult(
                models=models, model=chosen, method="models_list",
                cost_hint="通过模型列表接口验证，未消耗任何 token。")

        # 没有模型列表 → 必须知道要验哪个模型
        chosen = (model or self.spec.model_hint or "").strip()
        if not chosen:
            raise AIError(
                f"{self.spec.name_zh}没有提供模型列表接口，"
                "请先填写要使用的模型名（可从其控制台复制）。",
                kind="model_required")

        probe = self.chat(
            [{"role": "user", "content": "hi"}],
            model=chosen, max_tokens=1, temperature=0)
        return ValidationResult(
            models=[chosen], model=chosen, method="chat_probe",
            cost_hint=(f"{self.spec.name_zh}没有免费的模型列表接口，"
                       f"改用一次最小对话验证，消耗 "
                       f"{probe.usage.total_tokens} token。"),
            usage=probe.usage)

    # --- 对话 ---
    def chat(self, messages: list[dict], *, model: str, json_mode: bool = False,
             max_tokens: int = 800, temperature: float = 0.2) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # 厂商特有字段（如关闭深度思考）。放在最后合并，但不允许覆盖上面的核心字段——
        # spec 是用来补充差异的，不是用来改写协议的。
        for k, v in (self.spec.extra_body or {}).items():
            payload.setdefault(k, v)

        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(f"{self.base_url}/chat/completions",
                           headers=self._headers, json=payload)
        except httpx.RequestError as exc:
            raise AIError(f"连不上 {self.base_url}：网络不可达或被拦截。",
                          kind="network", retryable=True) from exc
        if r.status_code != 200:
            raise self._friendly(r.status_code, r.text)

        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise AIError("模型没有返回任何内容。", kind="empty_response")
        text = (choices[0].get("message") or {}).get("content") or ""

        u = data.get("usage") or {}
        # 各家报缓存命中的字段名不同：DeepSeek 用 prompt_cache_hit_tokens，
        # OpenAI 放在 prompt_tokens_details.cached_tokens 里。
        details = u.get("prompt_tokens_details") or {}
        return Completion(text=text, usage=Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
            cache_hit_tokens=int(u.get("prompt_cache_hit_tokens")
                                 or details.get("cached_tokens")
                                 or u.get("cached_tokens") or 0),
            model=str(data.get("model") or model),
        ))

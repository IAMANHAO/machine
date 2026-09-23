"""多服务商接入测试 —— DeepSeek / 火山方舟（豆包）/ 阿里百炼（千问）/ OpenAI。

四家都号称"兼容 OpenAI"，但兼容的**程度不一样**。这组测试守的就是那些差异
被正确处理，以及**差异没有变成对用户的隐瞒**：

1. **能力是探测出来的，不是声明死的** —— `/models` 真去问一次，404 才退回对话探针
2. **验证花没花钱必须说出来** —— 没有模型列表的服务商要发一次探针，得如实告知
3. **错误信息要指对地方** —— 余额不足的是百炼，就不能把人支去 DeepSeek 充值
4. **厂商特有字段只能补充协议，不能改写协议** —— extra_body 不得覆盖 messages/model
5. **多账号并存，切换不动凭据** —— 切回来还是原来那把 key
6. **老用户不因升级而丢绑定** —— v1 的单绑定格式要能迁移

这里不打真实 API：用 httpx 的 MockTransport 拦住请求，断言的是我们这一侧。
"""

from __future__ import annotations

import json

import httpx
import pytest

from server.ai import providers as P
from server.ai.client import AIError, Provider

FAKE_KEY = "sk-testonly-0123456789abcdefghijklmnop"


# --- 1. 服务商清单本身 -----------------------------------------------------

def test_every_provider_has_what_the_bind_dialog_needs():
    """绑定对话框要能告诉用户：去哪拿 key、填什么模型、这家有什么坑。
    缺一样，界面上就会出现一个填不下去的空。"""
    for spec in (P.DEEPSEEK, P.ARK, P.DASHSCOPE, P.OPENAI):
        assert spec.base_url.startswith("https://"), spec.id
        assert spec.console_url.startswith("https://"), spec.id
        assert spec.name_zh and spec.notes, spec.id
        assert spec.model_hint, spec.id


def test_registry_ids_match_credential_profiles():
    """spec.id 同时是系统凭据库里的 profile 名。两者错开会导致
    "绑定成功但读不到 key"——最难查的一类问题。"""
    for pid, spec in P.REGISTRY.items():
        assert pid == spec.id


def test_custom_provider_has_no_baked_in_endpoint():
    """自填服务不能有默认 base_url，否则用户不填也能"绑定成功"，
    实际打到一个他没选过的服务商去。"""
    assert P.OPENAI_COMPATIBLE.base_url == ""


def test_unknown_provider_falls_back_to_custom_not_deepseek():
    """认不出来的 id 当自填处理。悄悄退回 DeepSeek 会让用户
    以为绑的是 A，实际打给了 B。"""
    assert P.get("nonexistent").id == P.OPENAI_COMPATIBLE.id
    assert P.get("").id == P.OPENAI_COMPATIBLE.id
    assert P.get(None).id == P.OPENAI_COMPATIBLE.id


def test_thinking_is_off_by_default_for_hybrid_models():
    """方舟与百炼的模型默认会"深度思考"。本软件的三个 AI 任务都要结构化输出，
    思考模式只会多花钱、还可能破坏 JSON。两家的关法不一样，各按各的来。"""
    assert P.ARK.extra_body == {"thinking": {"type": "disabled"}}
    assert P.DASHSCOPE.extra_body == {"enable_thinking": False}
    # DeepSeek 与 OpenAI 不需要这个开关，就不该凭空塞字段
    assert P.DEEPSEEK.extra_body == {}
    assert P.OPENAI.extra_body == {}


def test_only_deepseek_claims_a_balance_endpoint():
    """OpenAI 早已下线面向 API key 的余额查询；方舟与百炼的文档也没有。
    声明了却查不到，界面上会一直显示"查询失败"。"""
    assert P.DEEPSEEK.balance_path
    for spec in (P.ARK, P.DASHSCOPE, P.OPENAI, P.OPENAI_COMPATIBLE):
        assert not spec.balance_path, spec.id


def test_ark_base_url_matches_the_official_doc():
    """出处：火山方舟官方「兼容 OpenAI SDK」文档给出的 base_url。
    写错一个字母就是连不上，而错误信息只会说"网络不可达"。"""
    assert P.ARK.base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert P.DASHSCOPE.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert P.DEEPSEEK.base_url == "https://api.deepseek.com"
    assert P.OPENAI.base_url == "https://api.openai.com/v1"


# --- 2. 请求怎么发 ---------------------------------------------------------

def _provider(spec, handler):
    """用 MockTransport 拦住请求，拿到真实发出去的 payload。"""
    p = Provider(FAKE_KEY, spec=spec)
    transport = httpx.MockTransport(handler)
    orig = httpx.Client

    class Patched(orig):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return p, Patched


def _capture(spec, monkeypatch, status=200, body=None):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        if request.content:
            seen["payload"] = json.loads(request.content)
        return httpx.Response(status, json=body if body is not None else {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            "model": "m",
        })

    p, Patched = _provider(spec, handler)
    monkeypatch.setattr(httpx, "Client", Patched)
    return p, seen


@pytest.mark.parametrize("spec", [P.ARK, P.DASHSCOPE])
def test_vendor_extra_body_reaches_the_request(spec, monkeypatch):
    p, seen = _capture(spec, monkeypatch)
    p.chat([{"role": "user", "content": "hi"}], model="m")
    for k, v in spec.extra_body.items():
        assert seen["payload"][k] == v, f"{spec.id} 的 {k} 没发出去"


def test_extra_body_cannot_overwrite_the_protocol(monkeypatch):
    """spec 是用来补充厂商差异的，不是用来改写协议的。
    一个写错的 extra_body 不该能把 messages 或 model 换掉。"""
    evil = P.ProviderSpec(
        id="evil", name_zh="坏的", base_url="https://example.com",
        console_url="https://example.com", model_hint="m", notes="",
        extra_body={"messages": [{"role": "user", "content": "被劫持"}],
                    "model": "别的模型", "stream": True})
    p, seen = _capture(evil, monkeypatch)
    p.chat([{"role": "user", "content": "真正的问题"}], model="真正的模型")
    assert seen["payload"]["model"] == "真正的模型"
    assert seen["payload"]["messages"][0]["content"] == "真正的问题"
    assert seen["payload"]["stream"] is False


@pytest.mark.parametrize("spec", [P.DEEPSEEK, P.ARK, P.DASHSCOPE, P.OPENAI])
def test_every_provider_uses_bearer_auth(spec, monkeypatch):
    p, seen = _capture(spec, monkeypatch)
    p.chat([{"role": "user", "content": "hi"}], model="m")
    assert seen["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    assert seen["url"].startswith(spec.base_url), spec.id


def test_openai_style_cached_tokens_are_counted(monkeypatch):
    """各家报缓存命中的字段名不同：DeepSeek 用 prompt_cache_hit_tokens，
    OpenAI 放在 prompt_tokens_details.cached_tokens 里。漏掉后者，
    用量页会把 OpenAI 的缓存命中一直显示成 0。"""
    p, _ = _capture(P.OPENAI, monkeypatch, body={
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110, "prompt_tokens_details": {"cached_tokens": 64}},
        "model": "gpt-4.1-mini"})
    out = p.chat([{"role": "user", "content": "hi"}], model="gpt-4.1-mini")
    assert out.usage.cache_hit_tokens == 64


# --- 3. 能力探测与验证代价 -------------------------------------------------

def test_missing_model_list_is_not_an_error(monkeypatch):
    """方舟与百炼的文档都没提模型列表接口。404 要当"这家没有"处理，
    而不是把绑定流程打断。"""
    p, _ = _capture(P.ARK, monkeypatch, status=404, body={"error": "not found"})
    assert p.list_models() == []


def test_real_errors_still_surface_when_listing_models(monkeypatch):
    """但 401 不是"这家没有模型列表"，是 key 不对——不能一起吞掉。"""
    p, _ = _capture(P.DEEPSEEK, monkeypatch, status=401, body={"error": "bad key"})
    with pytest.raises(AIError) as exc:
        p.list_models()
    assert exc.value.kind == "unauthorized"


def test_validation_says_when_it_costs_nothing(monkeypatch):
    p, _ = _capture(P.DEEPSEEK, monkeypatch, body={
        "data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})
    result = p.validate()
    assert result.method == "models_list"
    assert "未消耗" in result.cost_hint
    assert result.model == "deepseek-chat"


def test_validation_says_when_it_costs_tokens(monkeypatch):
    """没有模型列表就得发一次最小对话。**这花的是用户的钱**，
    哪怕只有几个 token，也必须说出来而不是悄悄花掉。"""
    p, seen = _capture(P.ARK, monkeypatch)
    result = p.validate("doubao-seed-2-1-pro-260628")
    assert result.method == "chat_probe"
    assert "token" in result.cost_hint
    assert result.usage and result.usage.total_tokens == 4
    assert seen["payload"]["max_tokens"] == 1, "探针必须尽可能小"


def test_provider_without_model_list_demands_a_model_name(monkeypatch):
    """既没有模型列表、用户又没填模型名时，要明确说"请填模型名"，
    而不是拿一个内置示例去试——那会让用户以为自己选过。"""
    spec = P.ProviderSpec(id="x", name_zh="某家", base_url="https://example.com",
                          console_url="https://example.com", model_hint="",
                          notes="", may_list_models=False)
    p, _ = _capture(spec, monkeypatch)
    with pytest.raises(AIError) as exc:
        p.validate()
    assert exc.value.kind == "model_required"


# --- 4. 错误信息指对地方 ---------------------------------------------------

@pytest.mark.parametrize("spec", [P.DEEPSEEK, P.ARK, P.DASHSCOPE, P.OPENAI])
def test_errors_name_the_provider_that_actually_failed(spec):
    """接了四家之后，"去充值"必须指对地方。早先这句话写死了 DeepSeek——
    余额不足的其实是百炼时，它会把用户支到一个根本没欠费的平台去。"""
    p = Provider(FAKE_KEY, spec=spec)
    for status in (401, 402, 403):
        assert spec.name_zh in str(p._friendly(status, "{}")), (spec.id, status)


def test_error_text_never_echoes_the_upstream_body():
    p = Provider(FAKE_KEY, spec=P.DEEPSEEK)
    body = '{"secret":"不该出现在界面上"}'
    for status in (401, 402, 403, 429):
        assert "不该出现在界面上" not in str(p._friendly(status, body))


def test_404_points_at_the_config_not_at_the_network():
    """base_url 或模型名写错时报 404。说成"网络不可达"会让人去查防火墙，
    而真正该改的是设置页里的两个输入框。"""
    p = Provider(FAKE_KEY, spec=P.ARK)
    msg = str(p._friendly(404, "{}"))
    assert "base_url" in msg and "模型名" in msg


# --- 5. 多账号并存（走 HTTP 接口） -----------------------------------------

def test_providers_endpoint_exposes_no_credentials(client, bind_as):
    """服务商清单是**接入事实**，不是账号信息。绑定之后再取也一样——
    这个接口任何时候都不该携带凭据。"""
    bind_as("deepseek")
    body = client.get("/api/account/providers").json()

    ids = {p["id"] for p in body["providers"]}
    assert {"deepseek", "ark", "dashscope", "openai", "custom"} <= ids
    assert body["default"] == "deepseek"

    blob = json.dumps(body, ensure_ascii=False)
    assert FAKE_KEY not in blob
    # 只允许出现声明过的公开字段，多一个都要有人解释清楚
    allowed = {"id", "name_zh", "base_url", "console_url", "model_hint",
               "notes", "has_balance", "may_list_models", "key_env_hint"}
    for item in body["providers"]:
        assert set(item) <= allowed, set(item) - allowed
        # key_env_hint 是官方文档里的**环境变量名**，不是值
        assert item["key_env_hint"].isupper() or item["key_env_hint"] == ""


def test_binding_two_providers_keeps_both(client, vault, bind_as):
    bind_as("deepseek")
    bind_as("ark", model="doubao-seed-2-1-pro-260628")

    acc = client.get("/api/account").json()
    bound = {b["provider"] for b in acc["bindings"]}
    assert bound == {"deepseek", "ark"}
    assert acc["active"] == "ark", "刚绑定的那家应当生效"
    # 两把 key 各占一个 profile，互不覆盖
    assert ("mds-selector", "deepseek") in vault
    assert ("mds-selector", "ark") in vault


def test_switching_active_account_does_not_touch_credentials(client, vault, bind_as):
    """切换不该动凭据——解绑才删 key。切回来还得是原来那把，
    否则用户每切一次就要回控制台重新复制。"""
    bind_as("deepseek")
    bind_as("ark", model="doubao-x")
    before = dict(vault)

    r = client.post("/api/account/activate", json={"provider": "deepseek"})
    assert r.status_code == 200
    assert client.get("/api/account").json()["active"] == "deepseek"
    assert dict(vault) == before, "切换账号动了凭据库"


def test_unbinding_one_leaves_the_other_working(client, vault, bind_as):
    bind_as("deepseek")
    bind_as("ark", model="doubao-x")

    client.request("DELETE", "/api/account", params={"provider": "ark"})
    acc = client.get("/api/account").json()
    assert {b["provider"] for b in acc["bindings"]} == {"deepseek"}
    assert acc["active"] == "deepseek", "解绑生效的那家之后要自动落到还剩的那家"
    assert ("mds-selector", "ark") not in vault
    assert ("mds-selector", "deepseek") in vault


def test_activating_an_unbound_provider_is_refused(client, bind_as):
    bind_as("deepseek")
    r = client.post("/api/account/activate", json={"provider": "dashscope"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_bound"


def test_health_reflects_whichever_account_is_active(client, bind_as):
    bind_as("deepseek")
    assert client.get("/api/health").json()["ai_bound"] is True
    bind_as("openai", model="gpt-4.1-mini")
    assert client.get("/api/account").json()["binding"]["provider"] == "openai"


# --- 6. 老用户不因升级而丢绑定 ---------------------------------------------

def test_v1_single_binding_file_is_migrated(tmp_path):
    """老版本的 account.json 整个文件就是一个 Binding。
    升级后不该让用户重新绑定——key 还在凭据库里，元数据也不该丢。"""
    from server.ai.credentials import load_account

    (tmp_path / "account.json").write_text(json.dumps({
        "profile": "deepseek", "provider": "deepseek",
        "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
        "label": "sk-tes********mnop", "bound_at": "2026-01-01T00:00:00+00:00",
        "models_seen": ["deepseek-chat"],
    }), encoding="utf-8")

    acc = load_account(tmp_path)
    assert acc.active == "deepseek"
    assert acc.get().model == "deepseek-chat"
    assert acc.get().label == "sk-tes********mnop"


def test_corrupt_account_file_degrades_to_unbound(tmp_path):
    """文件坏了就是没绑定，不该让整个服务起不来。"""
    from server.ai.credentials import load_account

    (tmp_path / "account.json").write_text("{ 这不是 json", encoding="utf-8")
    assert load_account(tmp_path).bindings == {}

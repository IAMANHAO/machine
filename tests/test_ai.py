"""账号绑定与 AI 层测试（M4）。

重点守四件事：
1. **未绑定时软件完整可用** —— AI 是增强，不是运行前提
2. **AI 建议绕不过校验闸门** —— 模型吐一个越界的数，必须被引擎当场挡下
3. **凭据不泄漏** —— 任何响应、日志、配置文件里都不能出现真实 key
4. **超限直接拒绝** —— 不静默继续花用户的钱

这里不打真实 API：provider 用假的，断言的是我们这一侧的边界行为。
"""

from __future__ import annotations

import json

import pytest

from conftest import FAKE_KEY, FakeProvider  # pytest 把 tests/ 加进了 sys.path

GOLDEN = {
    "P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
    "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
    "hours_per_day": "h_le_10",
}


def _bind(client):
    return client.post("/api/account/bind", json={"api_key": FAKE_KEY})


# --- 1. 未绑定时软件完整可用 -----------------------------------------------

def test_unbound_is_the_default_and_is_honest(client):
    acc = client.get("/api/account").json()
    assert acc["bound"] is False
    assert acc["effective_mode"] == "offline"
    assert acc["binding"] is None
    assert client.get("/api/health").json()["ai_bound"] is False


def test_full_selection_works_without_any_account(client):
    """M4 出口标准：全新安装、不绑定任何账号，0~6 全流程照常。"""
    body = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": GOLDEN}).json()
    assert body["trace"]["status"] == "ok"
    assert all(c["detail"]["passed"] for c in body["trace"]["checks"])
    assert body["procure"]["links"], "采购链接不该依赖 AI"


def test_intent_degrades_to_keyword_matching(client):
    r = client.post("/api/ai/intent", json={
        "text": "帮我选一根同步带，电机功率 5.5 kW，转速 1450 r/min，中心距 400 mm"}).json()
    assert r["source"] == "offline"
    assert r["material"] == "synchronous_belt"
    assert r["values"]["P"] == 5.5 and r["values"]["n1"] == 1450
    assert r["notes"], "离线降级必须说明自己是粗匹配"
    assert FakeProvider.calls == [], "未绑定时绝不能发起任何调用"


def test_intent_offline_admits_when_it_cannot_tell(client):
    r = client.post("/api/ai/intent", json={"text": "帮我选个能传扭矩的东西"}).json()
    assert r["material"] is None, "认不出来就该承认，不要瞎猜一个物料"


def test_suggest_degrades_to_spec_typicals(client):
    r = client.post("/api/ai/suggest", json={
        "material": "synchronous_belt", "known": {"P": 5.5}}).json()
    assert r["source"] == "offline"
    assert all("规格" in s["rationale"] for s in r["suggestions"].values())
    assert FakeProvider.calls == []


def test_explain_refuses_honestly_when_unbound(client):
    r = client.post("/api/ai/explain", json={"material": "synchronous_belt", "step": {}})
    assert r.status_code == 409
    msg = r.json()["detail"]["message"]
    assert "不受影响" in msg, "拒绝时要说清哪些功能照常可用"


# --- 2. 绑定流程 -----------------------------------------------------------

def test_bind_verifies_and_never_echoes_the_key(client, vault):
    r = _bind(client)
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == FakeProvider.models
    assert body["binding"]["model"] == "deepseek-flash"

    blob = json.dumps(body, ensure_ascii=False)
    assert FAKE_KEY not in blob, "响应里绝不能出现真实 key"
    assert body["binding"]["label"].startswith("sk-tes")
    assert "*" in body["binding"]["label"]


def test_bound_key_lives_only_in_the_credential_store(client, vault, tmp_path):
    _bind(client)
    assert list(vault.values()) == [FAKE_KEY], "key 应当只存在于凭据库"

    data_dir = tmp_path / "data"
    for path in data_dir.rglob("*"):
        if path.is_file():
            assert FAKE_KEY not in path.read_text(encoding="utf-8", errors="ignore"), \
                f"{path.name} 里出现了明文 key"


def test_models_are_not_hardcoded(client):
    """模型清单来自账号的 /models 接口，不是写死在代码里。"""
    FakeProvider.models = ["some-future-model"]
    try:
        body = _bind(client).json()
        assert body["binding"]["model"] == "some-future-model"
    finally:
        FakeProvider.models = ["deepseek-flash", "deepseek-v4-pro"]


def test_unbind_removes_the_key(client, vault):
    _bind(client)
    assert vault
    client.delete("/api/account")
    assert not vault, "解绑必须真的删掉凭据"
    assert client.get("/api/account").json()["bound"] is False


def test_forced_offline_mode_makes_no_calls_even_when_bound(client):
    _bind(client)
    FakeProvider.calls = []
    r = client.post("/api/ai/intent", json={"text": "同步带 5.5kW", "mode": "offline"}).json()
    assert r["source"] == "offline"
    assert FakeProvider.calls == [], "强制离线时不得发起任何网络调用"


def test_bound_mode_uses_ai(client):
    _bind(client)
    FakeProvider.calls = []
    r = client.post("/api/ai/intent", json={"text": "同步带 5.5kW"}).json()
    assert r["source"] == "ai"
    assert len(FakeProvider.calls) == 1
    assert FakeProvider.calls[0]["json_mode"] is True


# --- 3. AI 绕不过校验闸门（最重要的一条） ----------------------------------

def test_out_of_range_suggestion_is_rejected_by_the_engine(client):
    """模型吐一个越界的数，必须被当场挡下，而不是进到参数表里。"""
    _bind(client)
    FakeProvider.script["suggest"] = {
        "suggestions": {
            "P": {"value": 99999, "rationale": "我觉得可以"},          # 超出 max 500
            "a0": {"value": 400, "rationale": "推荐区间中部"},          # 合法
        },
        "skipped": {},
    }
    r = client.post("/api/ai/suggest", json={
        "material": "synchronous_belt", "known": {"n1": 1450}}).json()

    assert "P" not in r["suggestions"], "越界建议不能出现在可采用的列表里"
    assert "P" in r["rejected"]
    assert "校验" in r["rejected"]["P"]
    assert r["suggestions"]["a0"]["value"] == 400


def test_invalid_enum_suggestion_is_rejected(client):
    _bind(client)
    FakeProvider.script["suggest"] = {
        "suggestions": {"belt_type": {"value": "ZZZ", "rationale": "编的"}},
        "skipped": {},
    }
    r = client.post("/api/ai/suggest", json={
        "material": "synchronous_belt", "known": {"P": 5.5}}).json()
    assert "belt_type" not in r["suggestions"]
    assert "belt_type" in r["rejected"]


def test_suggestion_for_unknown_param_is_dropped(client):
    _bind(client)
    FakeProvider.script["suggest"] = {
        "suggestions": {"不存在的参数": {"value": 1, "rationale": "x"}}, "skipped": {}}
    r = client.post("/api/ai/suggest", json={
        "material": "synchronous_belt", "known": {"P": 5.5}}).json()
    assert r["suggestions"] == {}
    assert "不存在的参数" in r["rejected"]


def test_intent_cannot_invent_a_material(client):
    """模型返回一个引擎里根本没有的物料时，必须被丢掉。

    这里刻意用一个不存在的 id——早先用的是 gear，但 gear 已经有可执行工作流了，
    再拿它当反例只会让这条断言悄悄失效。"""
    _bind(client)
    FakeProvider.script["intent"] = {
        "material": "flux_capacitor", "values": {}, "unmatched": [], "notes": ""}
    r = client.post("/api/ai/intent", json={"text": "选个不存在的东西"}).json()
    assert r["material"] is None,         "引擎里没有的物料不能被当成有效物料返回"


def test_ai_suggested_values_still_go_through_run_validation(client):
    """把 AI 的建议原样喂给 /selection/run，越界照样 422 —— 没有后门。"""
    _bind(client)
    r = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "save": False,
        "values": {**GOLDEN, "P": 99999}})
    assert r.status_code == 422
    assert r.json()["detail"]["param"] == "P"


# --- 4. 用量与上限 ---------------------------------------------------------

def test_usage_is_recorded(client):
    _bind(client)
    client.post("/api/ai/intent", json={"text": "同步带 5.5kW"})
    usage = client.get("/api/account/limits").json()["usage_today"]
    assert usage["calls"] == 1
    assert usage["total_tokens"] == 150
    assert usage["cache_hit_tokens"] == 20


def test_daily_cap_refuses_instead_of_silently_continuing(client):
    _bind(client)
    client.put("/api/account/limits", json={
        "max_tokens_per_call": 1200, "max_calls_per_day": 1, "enabled": True})

    assert client.post("/api/ai/intent", json={"text": "同步带"}).status_code == 200
    r = client.post("/api/ai/intent", json={"text": "同步带"})
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["error"] == "BudgetExceeded"
    assert "计算与校核不受影响" in detail["message"]


def test_cap_still_leaves_the_engine_usable(client):
    """超了 AI 上限，选型照样能做完——这才叫"AI 是可选增强"。"""
    _bind(client)
    client.put("/api/account/limits", json={
        "max_tokens_per_call": 1200, "max_calls_per_day": 1, "enabled": True})
    client.post("/api/ai/intent", json={"text": "同步带"})            # 用掉唯一一次
    assert client.post("/api/ai/intent", json={"text": "x"}).status_code == 429

    body = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": GOLDEN, "save": False}).json()
    assert body["trace"]["status"] == "ok"
    assert body["procure"]["links"]


def test_limits_reject_nonsensical_values(client):
    """每日 0 次这种配置应该用 enabled=false 表达，而不是把上限设成 0。"""
    _bind(client)
    assert client.put("/api/account/limits", json={
        "max_tokens_per_call": 1200, "max_calls_per_day": 0,
        "enabled": True}).status_code == 422


def test_token_cap_is_applied_to_the_request(client):
    _bind(client)
    client.put("/api/account/limits", json={
        "max_tokens_per_call": 200, "max_calls_per_day": 100, "enabled": True})
    FakeProvider.calls = []
    client.post("/api/ai/intent", json={"text": "同步带 5.5kW"})
    assert FakeProvider.calls[0]["max_tokens"] == 200


# --- 5. 错误翻译 -----------------------------------------------------------

@pytest.mark.parametrize("status,needle", [
    (401, "重新绑定"),
    (402, "余额不足"),
    (429, "稍后再试"),
])
def test_upstream_errors_become_actionable_messages(status, needle):
    from server.ai.client import Provider
    from server.ai.providers import DEEPSEEK
    err = Provider("sk-x", spec=DEEPSEEK)._friendly(status, "{}")
    assert needle in str(err)
    assert "{}" not in str(err), "不要把上游响应体原样透出去"


@pytest.mark.parametrize("spec_name", ["ARK", "DASHSCOPE", "OPENAI"])
def test_error_messages_name_the_right_provider(spec_name):
    """接了四家之后，"去充值"必须指对地方。

    早先这句话写死了"DeepSeek 账号"——余额不足的其实是百炼时，
    它会把用户支到一个根本没欠费的平台去。"""
    from server.ai import providers
    from server.ai.client import Provider

    spec = getattr(providers, spec_name)
    err = Provider("sk-x", spec=spec)._friendly(402, "{}")
    assert spec.name_zh in str(err), "错误信息必须说清是哪一家"
    other = providers.DEEPSEEK.name_zh
    assert other not in str(err) or spec is providers.DEEPSEEK


def test_mask_never_leaks_enough_to_reconstruct():
    from server.ai.credentials import mask
    masked = mask(FAKE_KEY)
    assert FAKE_KEY not in masked
    assert masked.count("*") >= 8
    assert mask("") == "" and mask(None) == ""

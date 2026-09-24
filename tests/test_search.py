"""检索层 —— 三条路按可用性降级，产物是"允许 AI 引用的 URL 全集"。

检索本身不决定采纳（那是取证层的事），但它决定候选集的边界。
所以这里守两件事：**三家的响应形状解析正确**，**降级不吞掉失败原因**。
"""

from __future__ import annotations

import httpx
import pytest

from server import research as R
from server import search_providers as SP

KEY = "srp-testonly-0123456789abcdef"


def _client(spec, handler) -> SP.SearchClient:
    return SP.SearchClient(KEY, spec=spec,
                           transport=httpx.MockTransport(handler))


# ── 三家的响应形状 ────────────────────────────────────────────────

def test_serper_organic_results_are_parsed():
    def handler(request):
        assert request.method == "POST"
        assert request.headers["X-API-KEY"] == KEY
        return httpx.Response(200, json={"organic": [
            {"title": "平键 GB/T 1095", "link": "https://a.example/k",
             "snippet": "剖面尺寸", "position": 1},
        ]})

    hits = _client(SP.SERPER, handler).search("平键")
    assert [h.url for h in hits] == ["https://a.example/k"]
    assert hits[0].snippet == "剖面尺寸"
    assert hits[0].origin == "serper"


def test_tavily_uses_bearer_not_a_body_field():
    """Tavily 已废弃"key 放请求体"，dev 档的 key 会直接被拒。"""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"results": [
            {"title": "键", "url": "https://b.example/k", "content": "摘要"},
        ]})

    hits = _client(SP.TAVILY, handler).search("键", limit=5)
    assert seen["auth"] == f"Bearer {KEY}"
    assert "api_key" not in seen["body"]
    assert "max_results" in seen["body"]
    assert hits[0].url == "https://b.example/k"


def test_brave_is_a_get_with_a_subscription_token():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["token"] = request.headers.get("X-Subscription-Token")
        seen["q"] = request.url.params.get("q")
        return httpx.Response(200, json={"web": {"results": [
            {"title": "键", "url": "https://c.example/k", "description": "摘要"},
        ]}})

    hits = _client(SP.BRAVE, handler).search("平键 尺寸")
    assert seen == {"method": "GET", "token": KEY, "q": "平键 尺寸"}
    assert hits[0].url == "https://c.example/k"


def test_results_without_a_url_are_skipped_not_turned_into_empty_hits():
    def handler(request):
        return httpx.Response(200, json={"organic": [
            {"title": "没有链接的一条"},
            {"title": "好的", "link": "https://a.example/ok"},
            "这一项根本不是对象",
        ]})

    hits = _client(SP.SERPER, handler).search("x")
    assert [h.url for h in hits] == ["https://a.example/ok"]


def test_a_missing_results_node_is_empty_not_a_crash():
    """服务商改了响应结构时应当返回"没结果"，而不是 500。"""
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    assert _client(SP.SERPER, handler).search("x") == []


@pytest.mark.parametrize("status,kind", [
    (401, "unauthorized"), (403, "forbidden"),
    (429, "rate_limited"), (503, "upstream_error"),
])
def test_http_errors_name_the_search_service_that_failed(status, kind):
    def handler(request):
        return httpx.Response(status, text="nope")

    with pytest.raises(SP.SearchError) as exc:
        _client(SP.BRAVE, handler).search("x")
    assert exc.value.kind == kind
    assert "Brave" in str(exc.value)          # 别让用户跑去另一家排查


def test_unknown_provider_is_rejected():
    with pytest.raises(SP.SearchError) as exc:
        SP.get_spec("bing")
    assert exc.value.kind == "unknown_provider"


def test_google_cse_is_deliberately_absent():
    """已对新客户关闭、2027-01-01 停服。收录它等于写进一条明年过期的断言。"""
    assert "google" not in {s.id for s in SP.SPECS}


# ── 白名单站内目录（第 ③ 级，不需要任何 key） ──────────────────────

def test_whitelist_matches_on_the_local_catalog(tmp_path, monkeypatch):
    def fake_catalog(site, root, refresh=False):
        if site.domain != "mechtool.cn":
            return []
        return [{"url": "https://www.mechtool.cn/key.html", "title": "平键的剖面尺寸与键槽"},
                {"url": "https://www.mechtool.cn/oil.html", "title": "润滑油粘度等级"}]

    monkeypatch.setattr(SP, "site_catalog", fake_catalog)
    hits = SP.search_whitelist("平键 键槽 尺寸", tmp_path)
    assert hits and hits[0].url == "https://www.mechtool.cn/key.html"
    assert hits[0].origin == "site:mechtool.cn"


def test_whitelist_returns_nothing_rather_than_everything_when_unmatched(
        tmp_path, monkeypatch):
    monkeypatch.setattr(SP, "site_catalog", lambda s, r, refresh=False: [
        {"url": "https://www.mechtool.cn/oil.html", "title": "润滑油粘度等级"}])
    assert SP.search_whitelist("磁吸铁片", tmp_path) == []


def test_site_catalog_is_cached_and_reused(tmp_path, monkeypatch):
    calls = []

    def fake_links(url):
        calls.append(url)
        return [{"url": "https://www.mechtool.cn/a.html", "title": "甲"}]

    monkeypatch.setattr(SP, "_links_of", fake_links)
    site = R.SITES[0]
    first = SP.site_catalog(site, tmp_path)
    second = SP.site_catalog(site, tmp_path)
    assert first == second
    # 两次调用只抓了一轮索引页 —— 目录缓存住了
    assert len(calls) == len(site.index_urls)


def test_a_broken_catalog_cache_is_refetched_not_fatal(tmp_path, monkeypatch):
    site = R.SITES[0]
    path = SP._catalog_path(site.domain, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("这不是 YAML: [[[", encoding="utf-8")
    monkeypatch.setattr(SP, "_links_of", lambda url: [
        {"url": "https://www.mechtool.cn/a.html", "title": "甲"}])
    assert SP.site_catalog(site, tmp_path)


# ── 降级调度 ───────────────────────────────────────────────────────

def _hit(url: str) -> SP.Hit:
    return SP.Hit(title="t", url=url, origin="x")


def test_binding_wins_when_it_returns_results(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"organic": [
            {"title": "t", "link": "https://a.example/1"}]})

    out = SP.search("平键", root=tmp_path, client=_client(SP.SERPER, handler))
    assert out.rung == "binding"
    assert [h.url for h in out.hits] == ["https://a.example/1"]
    assert out.to_dict()["urls"] == ["https://a.example/1"]
    assert out.problems == []


def test_a_failing_binding_degrades_and_keeps_the_reason(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(429, text="slow down")

    monkeypatch.setattr(SP, "search_whitelist",
                        lambda q, root, limit=8: [_hit("https://www.mechtool.cn/x")])
    out = SP.search("平键", root=tmp_path, client=_client(SP.SERPER, handler))
    assert out.rung == "whitelist"
    # 失败原因不能被降级吞掉 —— 用户得知道自己的搜索额度用尽了
    assert any("Serper" in p for p in out.problems)


def test_provider_native_search_is_the_middle_rung(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"organic": []})

    out = SP.search("平键", root=tmp_path,
                    client=_client(SP.SERPER, handler),
                    provider_search=lambda q, n: [_hit("https://d.example/p")])
    assert out.rung == "provider"
    assert [h.url for h in out.hits] == ["https://d.example/p"]


def test_a_throwing_provider_search_does_not_break_the_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(SP, "search_whitelist",
                        lambda q, root, limit=8: [_hit("https://www.mechtool.cn/x")])

    def boom(q, n):
        raise RuntimeError("联网插件没开通")

    out = SP.search("平键", root=tmp_path, provider_search=boom)
    assert out.rung == "whitelist"
    assert any("联网插件没开通" in p for p in out.problems)


def test_nothing_found_anywhere_is_reported_honestly(tmp_path, monkeypatch):
    monkeypatch.setattr(SP, "search_whitelist", lambda q, root, limit=8: [])
    out = SP.search("磁吸铁片", root=tmp_path)
    assert out.rung == "none"
    assert out.hits == []
    assert len(out.problems) >= 1
    # 检索不到不是故障，是要如实告诉用户的事实
    assert "都没有结果" in out.detail


# ── 绑定：凭据只进系统凭据库 ───────────────────────────────────────

def test_bind_then_status_then_unbind(env, monkeypatch):
    _client_, vault = env

    def handler(request):
        return httpx.Response(200, json={"organic": [
            {"title": "平键", "link": "https://a.example/k", "snippet": "s"}]})

    real = SP.SearchClient
    monkeypatch.setattr(SP, "SearchClient", lambda key, *, spec, **kw: real(
        key, spec=spec, transport=httpx.MockTransport(handler)))

    out = SP.bind(KEY, provider="serper")
    assert out["binding"]["provider"] == "serper"
    assert out["binding"]["label"] != KEY        # 只回显脱敏形式
    assert KEY not in str(out)

    st = SP.status()
    assert st["bound"] and st["search_active"] == "serper"
    assert st["rung"] == "binding"
    assert st["bindings"][0]["key_present"]

    gone = SP.unbind()
    assert gone["unbound"] and gone["key_removed"]
    assert not SP.status()["bound"]


def test_search_key_never_appears_in_any_file_under_data_dir(env, monkeypatch):
    """key 只许待在系统凭据库里。落进任何文件都是不可接受的。"""
    _client_, vault = env

    def handler(request):
        return httpx.Response(200, json={"organic": [
            {"title": "t", "link": "https://a.example/k"}]})

    real = SP.SearchClient
    monkeypatch.setattr(SP, "SearchClient", lambda key, *, spec, **kw: real(
        key, spec=spec, transport=httpx.MockTransport(handler)))
    SP.bind(KEY, provider="serper")

    from server.config import data_dir
    for path in data_dir().rglob("*"):
        if path.is_file():
            assert KEY not in path.read_bytes().decode("utf-8", "replace"), path
    # 而凭据库里确实有它
    assert KEY in vault.values()


def test_unbinding_search_leaves_the_ai_account_alone(env, bind_as, monkeypatch):
    """两把 key 是两件事。解绑搜索不该把 AI 账号也弄掉。"""
    bind_as("deepseek")

    def handler(request):
        return httpx.Response(200, json={"organic": [
            {"title": "t", "link": "https://a.example/k"}]})

    real = SP.SearchClient
    monkeypatch.setattr(SP, "SearchClient", lambda key, *, spec, **kw: real(
        key, spec=spec, transport=httpx.MockTransport(handler)))
    SP.bind(KEY, provider="serper")
    SP.unbind()

    from server import ai
    assert ai.is_bound()
    assert not SP.status()["bound"]


def test_status_without_a_binding_says_it_will_use_the_whitelist(env):
    st = SP.status()
    assert not st["bound"]
    assert st["rung"] == "whitelist"
    # mechtool.cn 必须出现在白名单里，并且是 trusted 档
    trusted = [w for w in st["whitelist"] if w["tier"] == "trusted"]
    assert [w["domain"] for w in trusted] == ["mechtool.cn"]

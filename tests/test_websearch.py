"""服务商自带的联网检索（降级阶梯第 ② 级）。

守两件事：

1. **只取链接，不取答案。** 模型写的正文一个字都不进结果——
   整条流水线的其余部分都建立在"服务器自己抓回原文核对"之上。
2. **不支持就老实降级。** 400/404 不是故障，是"这家/这个模型不会上网"，
   要让调度器退到白名单，而不是把整个引导流程炸掉。
"""

from __future__ import annotations

import httpx
import pytest

from server.ai import websearch as W

KEY = "sk-testonly-abcdef0123456789"


def _t(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ── Anthropic Messages 协议（DeepSeek 走这条）──────────────────────

ANTHROPIC_BODY = {
    "role": "assistant",
    "content": [
        {"type": "text", "text": "我来搜一下平键的标准。"},
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "平键 GB/T 1095"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
            {"type": "web_search_result", "url": "https://www.mechtool.cn/key.html",
             "title": "平键的剖面尺寸", "page_age": "2025-01-01",
             "encrypted_content": "xxxx"},
            {"type": "web_search_result", "url": "https://openstd.samr.gov.cn/x",
             "title": "GB/T 1095-2003", "page_age": "2024-09-09"},
        ]},
        {"type": "text", "text": "平键的剖面尺寸见 GB/T 1095-2003 表 1。",
         "citations": [{"type": "web_search_result_location",
                        "url": "https://www.mechtool.cn/key.html",
                        "cited_text": "b×h 的取值按轴径…"}]},
    ],
}


def test_deepseek_search_goes_to_the_anthropic_endpoint():
    """DeepSeek 的联网不在 OpenAI 兼容路径上，走 /anthropic/v1/messages。"""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["ver"] = request.headers.get("anthropic-version")
        import json
        seen["tools"] = json.loads(request.content)["tools"]
        return httpx.Response(200, json=ANTHROPIC_BODY)

    hits = W._anthropic_search("https://api.deepseek.com/anthropic", KEY,
                               "deepseek-v4-pro", "平键", 8, "provider:deepseek",
                               transport=_t(handler))
    assert seen["url"] == "https://api.deepseek.com/anthropic/v1/messages"
    assert seen["key"] == KEY
    assert seen["ver"] == "2023-06-01"
    assert seen["tools"][0]["type"] == "web_search_20250305"
    assert [h.url for h in hits] == ["https://www.mechtool.cn/key.html",
                                     "https://openstd.samr.gov.cn/x"]
    assert hits[0].origin == "provider:deepseek"


def test_only_the_links_are_taken_never_the_models_prose():
    """模型写的正文不能当依据，哪怕它是联网读来的。"""
    hits = W._anthropic_search("https://x.example", KEY, "m", "平键", 8, "o",
                               transport=_t(lambda r: httpx.Response(
                                   200, json=ANTHROPIC_BODY)))
    blob = " ".join(f"{h.title} {h.snippet}" for h in hits)
    assert "表 1" not in blob                  # 那句结论没有混进来
    assert all(h.url.startswith("http") for h in hits)


def test_a_search_error_block_yields_nothing_rather_than_garbage():
    """出错时 content 是单个 error 对象而不是列表 —— 别把它当结果解析。"""
    body = {"content": [{"type": "web_search_tool_result", "tool_use_id": "t",
                         "content": {"type": "web_search_tool_result_error",
                                     "error_code": "max_uses_exceeded"}}]}
    hits = W._anthropic_search("https://x.example", KEY, "m", "q", 8, "o",
                               transport=_t(lambda r: httpx.Response(200, json=body)))
    assert hits == []


def test_limit_is_respected():
    hits = W._anthropic_search("https://x.example", KEY, "m", "q", 1, "o",
                               transport=_t(lambda r: httpx.Response(
                                   200, json=ANTHROPIC_BODY)))
    assert len(hits) == 1


@pytest.mark.parametrize("status", [400, 404, 405])
def test_an_account_without_web_search_degrades_instead_of_exploding(status):
    with pytest.raises(W.Unsupported):
        W._anthropic_search("https://x.example", KEY, "m", "q", 8, "o",
                            transport=_t(lambda r: httpx.Response(status, text="no")))


# ── OpenAI Responses ───────────────────────────────────────────────

def test_openai_url_citations_are_parsed_and_deduped():
    body = {"output": [{"content": [{"annotations": [
        {"type": "url_citation", "url": "https://a.example/1", "title": "甲"},
        {"type": "url_citation", "url": "https://a.example/1", "title": "甲（重复）"},
        {"type": "file_citation", "url": "https://ignored.example"},
        {"type": "url_citation", "url": "https://b.example/2", "title": "乙"},
    ]}]}]}
    hits = W._openai_search("https://api.openai.com/v1", KEY, "gpt", "q", 8, "o",
                            transport=_t(lambda r: httpx.Response(200, json=body)))
    assert [h.url for h in hits] == ["https://a.example/1", "https://b.example/2"]


# ── 阿里百炼 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"search_info": {"search_results": [
        {"url": "https://a.example/1", "title": "甲", "site_name": "站甲"}]}},
    {"output": {"search_info": {"search_results": [
        {"url": "https://a.example/1", "title": "甲", "site_name": "站甲"}]}}},
])
def test_dashscope_source_list_is_found_in_either_shape(body):
    """承载来源的字段各版本有出入 —— 与其写死一条，不如容错。"""
    hits = W._dashscope_search("https://dashscope.example/v1", KEY, "qwen",
                               "q", 8, "o",
                               transport=_t(lambda r: httpx.Response(200, json=body)))
    assert [h.url for h in hits] == ["https://a.example/1"]


def test_dashscope_without_a_source_list_returns_nothing():
    hits = W._dashscope_search("https://d.example/v1", KEY, "qwen", "q", 8, "o",
                               transport=_t(lambda r: httpx.Response(
                                   200, json={"choices": [{"message": {"content": "答案"}}]})))
    assert hits == []


# ── 选适配器 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,supported", [
    ("deepseek", True), ("openai", True), ("dashscope", True),
    ("anthropic", True),
    ("ark", False),          # 联网走 Bot 端点，与这三家不是一回事
    ("custom", False),
    ("", False),
])
def test_which_providers_get_an_adapter(provider, supported):
    fn = W.searcher_for(provider, "https://x.example", KEY, "m")
    assert (fn is not None) is supported


def test_no_binding_means_no_provider_search(env):
    """没绑账号时这一级直接不存在，不去猜。"""
    assert W.current_searcher() is None


def test_the_dispatcher_degrades_when_the_provider_cannot_search(tmp_path, monkeypatch):
    """第 ② 级抛 Unsupported 时要退到第 ③ 级，并把原因留在 problems 里。"""
    from server import search_providers as SP

    monkeypatch.setattr(SP, "search_whitelist", lambda q, root, limit=8: [
        SP.Hit(title="t", url="https://www.mechtool.cn/x", origin="site:mechtool.cn")])

    def cannot(q, n):
        raise W.Unsupported("HTTP 404：这个模型不支持 web_search")

    out = SP.search("平键", root=tmp_path, provider_search=cannot)
    assert out.rung == "whitelist"
    assert any("web_search" in p for p in out.problems)

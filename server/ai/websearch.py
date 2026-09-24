"""server.ai.websearch —— 用 AI 服务商自带的联网能力做检索（降级阶梯的第 ② 级）。

引导式选型的阶段 1 要真的联网。三条路按可用性降级：

    ① 用户绑定的搜索服务（server/search_providers.py）
    ② **服务商自带的联网能力 —— 就是这里**
    ③ 白名单站点的本地目录

第 ② 级的价值很具体：**绑了 DeepSeek 的人不必再绑第二把 key。**

## 它只取"链接"，不取"答案"

这里**不要**模型的总结，只要它搜到的那批 URL 与标题。理由是整条流水线的
其余部分都建立在"服务器自己抓回原文核对"之上（`server/research.py`）——
模型的转述不能当依据，哪怕它是联网读来的。

所以这一层的产物与第 ①③ 级完全一样：一批 `Hit`。后面的取证一视同仁。

## 能力是探测出来的，不是声明死的

这是 `ai/providers.py` 立下的规矩（"把某家不支持某接口写死在代码里，
迟早会变成一条过期的断言"）。这里照办：**真的发一次请求**，
400/404 就当这家/这个模型不支持，返回空让调度器降级，不抛异常。

## 接口事实的出处

- **DeepSeek**（2026-09-24 核实）：官方「Anthropic API 兼容」文档
  （`https://api-docs.deepseek.com/guides/anthropic_api`）的兼容性表里
  `array, type = "web_search_tool_result"` 标为 Supported，端点
  `https://api.deepseek.com/anthropic`，走 Anthropic Messages 协议。
  **注意它不在我们平时用的 OpenAI 兼容路径上**，所以要单独一个适配器。
- **Anthropic Messages 的 web_search 工具**（2026-09-24 核实）：
  `tools: [{"type": "web_search_20250305", "name": "web_search", "max_uses": N}]`，
  响应里 `content[]` 含 `{"type": "web_search_tool_result", "content": [
  {"type": "web_search_result", "url", "title", "page_age", "encrypted_content"}]}`；
  出错时 `content` 是单个 `web_search_tool_result_error` 对象而不是列表。
- **OpenAI**：Responses API 的 `web_search` 工具，结果以 `url_citation`
  注解回来。**未在本机实测**（没有 OpenAI key），按文档实现，
  探测失败会自动降级。
- **阿里百炼**：OpenAI 兼容路径上的 `enable_search` / `search_options`。
  响应里承载来源的字段各版本有出入，这里按几个可能的位置容错解析。
  **未在本机实测。**
- **火山方舟**：联网内容要走「应用/Bot」端点而不是 chat.completions，
  与本模块的其余三家不是一回事。**暂不实现**——宁可这一级对方舟返回"不支持"、
  老实降级到白名单，也不要写一段没法验证的代码假装它能用。
"""

from __future__ import annotations

from typing import Callable

import httpx

TIMEOUT = 45.0          # 联网检索比普通对话慢得多
MAX_USES = 3

# 只要链接，不要答案。这句话要写死在提示里 —— 模型的转述不能当依据。
_PROMPT = ("请检索下面这个主题的权威资料，优先国家标准原文、机械设计手册的转载、"
           "以及 mechtool.cn。**只需要检索，不要总结、不要回答、不要给结论。**\n\n主题：")


def _hit(title: str, url: str, snippet: str, origin: str):
    from ..search_providers import Hit
    return Hit(title=title or url, url=url, snippet=snippet[:400], origin=origin)


# --- Anthropic Messages 协议（DeepSeek 走这条） ------------------------------

def _anthropic_search(base_url: str, api_key: str, model: str, query: str,
                      limit: int, origin: str,
                      transport: httpx.BaseTransport | None = None) -> list:
    """走 Anthropic Messages + web_search 工具，把搜到的链接捞出来。

    只解析 `web_search_tool_result` 块里的结果，**不读模型写的正文**。
    """
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": _PROMPT + query}],
        "tools": [{"type": "web_search_20250305", "name": "web_search",
                   "max_uses": MAX_USES}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json",
               # DeepSeek 的兼容端点按 Anthropic 协议收 key；有的网关只认 Bearer，
               # 两个都带上不会有副作用。
               "Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        resp = client.post(f"{base_url.rstrip('/')}/v1/messages",
                           headers=headers, json=body)
    if resp.status_code != 200:
        raise _unsupported(resp.status_code, resp.text)

    out: list = []
    for block in (resp.json().get("content") or []):
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, list):
            # 出错时 content 是单个 error 对象。当作"这次没搜到"，让调度器降级。
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = str(item.get("url") or "")
            if url:
                out.append(_hit(str(item.get("title") or ""), url,
                                str(item.get("page_age") or ""), origin))
            if len(out) >= limit:
                return out
    return out


# --- OpenAI Responses API ---------------------------------------------------

def _openai_search(base_url: str, api_key: str, model: str, query: str,
                   limit: int, origin: str,
                   transport: httpx.BaseTransport | None = None) -> list:
    """Responses API 的 web_search 工具。结果以 url_citation 注解回来。"""
    body = {"model": model, "input": _PROMPT + query,
            "tools": [{"type": "web_search"}]}
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        resp = client.post(f"{base_url.rstrip('/')}/responses",
                           headers={"Authorization": f"Bearer {api_key}",
                                    "Content-Type": "application/json"},
                           json=body)
    if resp.status_code != 200:
        raise _unsupported(resp.status_code, resp.text)

    out: list = []
    seen: set[str] = set()
    for item in (resp.json().get("output") or []):
        for block in (item.get("content") or []) if isinstance(item, dict) else []:
            for note in (block.get("annotations") or []) if isinstance(block, dict) else []:
                if not isinstance(note, dict) or note.get("type") != "url_citation":
                    continue
                url = str(note.get("url") or "")
                if url and url not in seen:
                    seen.add(url)
                    out.append(_hit(str(note.get("title") or ""), url, "", origin))
                if len(out) >= limit:
                    return out
    return out


# --- 阿里百炼：OpenAI 兼容路径上的 enable_search -----------------------------

# 承载来源清单的字段在不同版本/模型上不完全一致，这里按几个可能的位置找。
# 与其写死一条，不如容错——找不到就返回空，让调度器降级到白名单。
_DASHSCOPE_PATHS = (
    ("search_info", "search_results"),
    ("output", "search_info", "search_results"),
)


def _dashscope_search(base_url: str, api_key: str, model: str, query: str,
                      limit: int, origin: str,
                      transport: httpx.BaseTransport | None = None) -> list:
    body = {"model": model,
            "messages": [{"role": "user", "content": _PROMPT + query}],
            "enable_search": True,
            "search_options": {"enable_source": True, "forced_search": True}}
    with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                           headers={"Authorization": f"Bearer {api_key}",
                                    "Content-Type": "application/json"},
                           json=body)
    if resp.status_code != 200:
        raise _unsupported(resp.status_code, resp.text)

    data = resp.json()
    results = None
    for path in _DASHSCOPE_PATHS:
        node: object = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list) and node:
            results = node
            break
    if not results:
        return []

    out: list = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if url:
            out.append(_hit(str(item.get("title") or ""), url,
                            str(item.get("site_name") or ""), origin))
        if len(out) >= limit:
            break
    return out


class Unsupported(Exception):
    """这家 / 这个模型没有可用的联网检索。**不是故障**，调度器据此降级。"""


def _unsupported(status: int, body: str) -> Unsupported:
    return Unsupported(f"HTTP {status}：{body[:160]}")


# --- 按绑定挑适配器 ---------------------------------------------------------

def searcher_for(provider: str, base_url: str, api_key: str,
                 model: str) -> Callable[[str, int], list] | None:
    """返回 `(query, limit) -> list[Hit]`；这家没有可用的联网能力就返回 None。

    **这里只决定"试哪一条路"，不断言"它一定行"。** 真的行不行，
    要等 `server_search()` 发出去那一次请求才知道。
    """
    pid = (provider or "").strip().lower()
    root = (base_url or "").rstrip("/")

    if pid == "deepseek":
        # 联网在 Anthropic 兼容端点上，不在我们平时用的 OpenAI 兼容路径。
        return lambda q, n: _anthropic_search(
            f"{root}/anthropic", api_key, model, q, n, "provider:deepseek")
    if pid == "anthropic":
        return lambda q, n: _anthropic_search(
            root, api_key, model, q, n, "provider:anthropic")
    if pid == "openai":
        return lambda q, n: _openai_search(
            root, api_key, model, q, n, "provider:openai")
    if pid == "dashscope":
        return lambda q, n: _dashscope_search(
            root, api_key, model, q, n, "provider:dashscope")
    # 方舟的联网走 Bot 端点，与这三家不是一回事；没实测过的代码不写进来。
    return None


def current_searcher() -> Callable[[str, int], list] | None:
    """按当前生效的 AI 绑定给一个检索函数。没绑定或这家不支持就返回 None。"""
    from . import binding, read_key

    b = binding()
    if b is None:
        return None
    key = read_key(b.provider)
    if not key:
        return None
    return searcher_for(b.provider, b.base_url, key, b.model)

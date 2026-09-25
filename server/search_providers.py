"""server.search_providers —— 找候选依据：三条路，按可用性降级。

引导式选型的阶段 1 要"真的联网检索"。检索本身有三条路，能用哪条就用哪条：

| 级 | 路径 | 需要什么 |
|---|---|---|
| ① | 用户绑定的搜索服务（Tavily / Brave / Serper） | 用户自己的一把搜索 key |
| ② | AI 服务商自带的联网能力 | 见 `server/ai/websearch.py`（第 4 波） |
| ③ | 白名单站点本地目录 | 什么都不要 |

三条路的产物是同一种东西：一批 `Hit`（标题 + URL + 摘要）。
**这批 URL 就是后面允许 AI 引用的全集** —— `research.keep_known_urls()`
会把不在其中的引用剔掉。检索层决定了取证层的边界，所以它不能返回垃圾。

## 本模块不含任何凭据

和 `server/ai/providers.py` 一样，这里登记的只是"去哪调、怎么调"。
key 由用户自己在各家平台创建，存进系统凭据库（`server/ai/credentials.py`）。

## 接口事实的出处（2026-09-24 核实）

- **Tavily**：`POST https://api.tavily.com/search`，认证 `Authorization: Bearer <key>`，
  请求体 `{query, max_results, search_depth, include_domains, ...}`，
  响应 `results[]`，每项 `{title, url, content, published_date}`。
  **旧的"把 key 放在请求体 `api_key` 字段"已废弃**，dev 档的 key 会直接拒。
  免费额度 1000 credits/月。2026-02-10 被 Nebius 收购，价格与产品可能变。
- **Brave**：`GET https://api.search.brave.com/res/v1/web/search?q=...&count=N`，
  认证 `X-Subscription-Token: <key>`，响应 `web.results[]`，
  每项 `{title, url, description}`。**2026-02-12 起取消了永久免费档**，
  改为每个套餐每月 $5 额度——绑定对话框里必须写清这一点。
- **Serper**：`POST https://google.serper.dev/search`，认证 `X-API-KEY: <key>`，
  请求体 `{q, gl, hl, num, page}`，响应 `organic[]`，
  每项 `{title, link, snippet, position}`。注册赠 2500 次，之后按量计费。

**不收 Google Custom Search JSON API**：已对新客户关闭，2027-01-01 停服。
把一个明年就没有的接口写进产品，正是这个项目最该避免的"过期断言"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from . import research

TIMEOUT = 12.0
DEFAULT_LIMIT = 8
# 站点目录的缓存有效期。手册站的目录不会天天变，但也不该一年不看。
CATALOG_TTL = timedelta(days=7)


# --- 检索结果 ---------------------------------------------------------------

@dataclass
class Hit:
    """一条检索结果。`origin` 记下它是从哪条路来的，界面要如实显示。"""

    title: str
    url: str
    snippet: str = ""
    origin: str = ""          # tavily | brave | serper | site:<domain> | provider:<id>

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url,
                "snippet": self.snippet, "origin": self.origin}


class SearchError(Exception):
    """检索失败。message 面向用户。"""

    def __init__(self, message: str, *, kind: str = "search_failed",
                 status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


# --- 服务商登记表 -----------------------------------------------------------

@dataclass(frozen=True)
class SearchSpec:
    """一家搜索服务的接入事实。不含任何凭据。"""

    id: str
    name_zh: str
    endpoint: str
    method: str                  # GET | POST
    auth_header: str
    auth_prefix: str             # "Bearer " 或空
    query_field: str             # 请求里放查询词的字段/参数名
    limit_field: str             # 放条数的字段/参数名
    results_path: tuple[str, ...]  # 响应里到结果数组的路径
    title_key: str
    url_key: str
    snippet_key: str
    console_url: str
    # 绑定对话框里必须让用户看到的话。**纯文本，界面不解析 markdown。**
    notes: str
    key_env_hint: str = ""


TAVILY = SearchSpec(
    id="tavily",
    name_zh="Tavily",
    endpoint="https://api.tavily.com/search",
    method="POST",
    auth_header="Authorization",
    auth_prefix="Bearer ",
    query_field="query",
    limit_field="max_results",
    results_path=("results",),
    title_key="title", url_key="url", snippet_key="content",
    console_url="https://app.tavily.com/",
    key_env_hint="TAVILY_API_KEY",
    notes=("面向 AI 检索设计，返回的摘要比较干净。免费额度 1000 credits/月，"
           "不需要信用卡。注意：2026-02 已被 Nebius 收购，价格与额度后续可能调整。"),
)

BRAVE = SearchSpec(
    id="brave",
    name_zh="Brave Search",
    endpoint="https://api.search.brave.com/res/v1/web/search",
    method="GET",
    auth_header="X-Subscription-Token",
    auth_prefix="",
    query_field="q",
    limit_field="count",
    results_path=("web", "results"),
    title_key="title", url_key="url", snippet_key="description",
    console_url="https://api-dashboard.search.brave.com/",
    key_env_hint="BRAVE_API_KEY",
    notes=("独立索引，不转发 Google。注意：2026-02-12 起取消了永久免费档，"
           "改为每个套餐每月 5 美元额度——超出会计费到你自己的账号。"),
)

SERPER = SearchSpec(
    id="serper",
    name_zh="Serper",
    endpoint="https://google.serper.dev/search",
    method="POST",
    auth_header="X-API-KEY",
    auth_prefix="",
    query_field="q",
    limit_field="num",
    results_path=("organic",),
    title_key="title", url_key="link", snippet_key="snippet",
    console_url="https://serper.dev/",
    key_env_hint="SERPER_API_KEY",
    notes=("转发 Google 自然搜索结果，对中文手册站的召回通常最好。"
           "注册赠 2500 次，之后按量计费，单价是三家里最低的。"),
)

SPECS: tuple[SearchSpec, ...] = (SERPER, TAVILY, BRAVE)
_BY_ID = {s.id: s for s in SPECS}
DEFAULT_PROVIDER = SERPER.id


def get_spec(provider: str) -> SearchSpec:
    spec = _BY_ID.get((provider or "").strip().lower())
    if spec is None:
        raise SearchError(
            f"不认识的搜索服务 {provider!r}。可用：{', '.join(_BY_ID)}",
            kind="unknown_provider")
    return spec


def catalog() -> list[dict]:
    """给设置页用的服务商清单。"""
    return [{"id": s.id, "name_zh": s.name_zh, "console_url": s.console_url,
             "notes": s.notes, "key_env_hint": s.key_env_hint,
             "endpoint": s.endpoint} for s in SPECS]


# --- ① 绑定的搜索服务 -------------------------------------------------------

class SearchClient:
    """一家搜索服务的最小客户端。"""

    def __init__(self, api_key: str, *, spec: SearchSpec,
                 timeout: float = TIMEOUT,
                 transport: httpx.BaseTransport | None = None):
        if not api_key or not api_key.strip():
            raise SearchError("没有可用的搜索 key —— 请先在设置页绑定搜索服务。",
                              kind="not_bound")
        self._key = api_key.strip()
        self.spec = spec
        self.timeout = timeout
        # 测试注入 MockTransport 用。生产为 None，走 httpx 默认传输。
        self._transport = transport

    def __repr__(self) -> str:  # pragma: no cover - 防止 key 出现在堆栈里
        from .ai.credentials import mask
        return f"<SearchClient {self.spec.id} key={mask(self._key)}>"

    @property
    def _headers(self) -> dict:
        return {self.spec.auth_header: f"{self.spec.auth_prefix}{self._key}",
                "Accept": "application/json",
                "Content-Type": "application/json"}

    def _friendly(self, status: int, body: str) -> SearchError:
        who = self.spec.name_zh
        hint = {
            401: f"搜索 key 无效或已撤销。请在设置页重新绑定{who}。",
            403: f"这把 key 没有权限（{who}）。可能是套餐未开通或额度已尽。",
            422: f"{who}拒绝了这次查询（参数不合法）。",
            429: f"{who}限流或额度用尽，稍后再试。",
        }.get(status)
        if hint:
            kind = {401: "unauthorized", 403: "forbidden",
                    422: "bad_request", 429: "rate_limited"}[status]
            return SearchError(hint, kind=kind, status=status)
        if 500 <= status < 600:
            return SearchError(f"{who}服务端暂时不可用（{status}）。",
                               kind="upstream_error", status=status)
        return SearchError(f"调用{who}失败（HTTP {status}）：{body[:160]}",
                           status=status)

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[Hit]:
        spec = self.spec
        try:
            with httpx.Client(timeout=self.timeout,
                              transport=self._transport) as client:
                if spec.method == "GET":
                    resp = client.get(spec.endpoint, headers=self._headers,
                                      params={spec.query_field: query,
                                              spec.limit_field: limit})
                else:
                    resp = client.post(spec.endpoint, headers=self._headers,
                                       json={spec.query_field: query,
                                             spec.limit_field: limit})
        except httpx.RequestError as exc:
            raise SearchError(
                f"连不上{spec.name_zh}：网络不可达或被拦截。",
                kind="network") from exc
        if resp.status_code != 200:
            raise self._friendly(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchError(f"{spec.name_zh}返回的不是 JSON。",
                              kind="bad_response") from exc
        return _hits_from(data, spec)

    def validate(self) -> dict:
        """绑定验证：发一次最小查询。**会消耗一次配额**，对话框里已写明。"""
        hits = self.search("GB/T 1095 平键 剖面尺寸", limit=3)
        return {"ok": True, "provider": self.spec.id,
                "hits": len(hits),
                "sample": [h.to_dict() for h in hits[:3]]}


def _hits_from(data: dict, spec: SearchSpec) -> list[Hit]:
    node: object = data
    for key in spec.results_path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    if not isinstance(node, list):
        return []
    out: list[Hit] = []
    for raw in node:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get(spec.url_key) or "").strip()
        if not url:
            continue
        out.append(Hit(title=str(raw.get(spec.title_key) or "").strip() or url,
                       url=url,
                       snippet=str(raw.get(spec.snippet_key) or "").strip()[:400],
                       origin=spec.id))
    return out


# --- ③ 白名单站点本地目录 ---------------------------------------------------

def _catalog_path(domain: str, root: Path) -> Path:
    """站点目录缓存**不能**放进 `knowledge/cache/`。

    那底下的每个子目录都被 `mds.knowledge.Knowledge.materials()` 当成一个物料，
    于是 `_site_catalog` 会冒充成第 26 个"物料"、它的目录文件冒充成一张数据表，
    把知识库自检的张数顶高一张。打包冒烟里那条"随包 25 张表"就是这么红的。

    检索缓存和知识库是两件事，分开放。
    """
    return root / "search_cache" / f"{domain}.yaml"


def site_catalog(site: research.Site, root: Path, *,
                 refresh: bool = False) -> list[dict]:
    """一个白名单站点的本地目录 [{url, title}]。

    这些站点（mechtool.cn 尤其）**没有关键词检索接口**，所以先抓它登记的
    索引页，把 <a> 收成一份目录缓存下来，再在本地做关键词匹配。

    抓不到就返回空列表，**不抛异常**：站点挂了不该让整个引导流程中断，
    降级到"这一级没有结果"由上层决定下一步。
    """
    path = _catalog_path(site.domain, root)
    if not refresh and path.exists():
        try:
            cached = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            fetched = cached.get("fetched_at") or ""
            entries = cached.get("entries") or []
            if entries and fetched:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched)
                if age < CATALOG_TTL:
                    return list(entries)
        except (ValueError, yaml.YAMLError, OSError):
            pass   # 缓存坏了就当没有，重抓一份

    entries: list[dict] = []
    seen: set[str] = set()
    for index_url in site.index_urls:
        # 走 _links_of 而不是 research.fetch：后者只留正文，链接已经被丢掉了。
        # 索引页只需要 <a>，抓一次就够——别为同一页抓两遍。
        for link in _links_of(index_url):
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            entries.append(link)

    if entries:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(
            {"site": site.domain,
             "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "entries": entries},
            allow_unicode=True, sort_keys=False), encoding="utf-8")
    return entries


def _links_of(url: str) -> list[dict]:
    """抓一页并抽出同站链接。抓不到返回空。"""
    try:
        safe = research.guard_url(url)
    except research.FetchError:
        return []
    try:
        with httpx.Client(timeout=research.TIMEOUT, follow_redirects=False,
                          headers={"User-Agent": research.USER_AGENT}) as client:
            resp = client.get(safe)
    except httpx.RequestError:
        return []
    if resp.status_code != 200:
        return []
    return research.extract_links(resp.text, safe)


# 分词：ASCII 单词按空白切，中文没有空格，退化成 2-gram。
# 这是个**粗糙**的匹配器，只求把明显相关的条目排上来；
# 排不准的代价由取证层兜着——检索只决定候选集，不决定采纳。
_WORD_RE = re.compile(r"[A-Za-z0-9./]+")
_CJK_RE = re.compile(r"[一-鿿]+")


def _tokens(text: str) -> list[str]:
    out = [w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= 2]
    for run in _CJK_RE.findall(text or ""):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def search_whitelist(query: str, root: Path,
                     limit: int = DEFAULT_LIMIT) -> list[Hit]:
    """在白名单站点的本地目录里做关键词匹配。不需要任何 key。"""
    terms = set(_tokens(query))
    if not terms:
        return []
    scored: list[tuple[int, Hit]] = []
    for site in research.SITES:
        if not site.index_urls:
            continue
        for entry in site_catalog(site, root):
            hits = sum(1 for t in terms if t in _tokens(entry["title"]))
            if hits:
                scored.append((hits, Hit(
                    title=entry["title"], url=entry["url"],
                    snippet=f"{site.name_zh} 站内目录条目",
                    origin=f"site:{site.domain}")))
    scored.sort(key=lambda p: -p[0])
    return [h for _, h in scored[:limit]]


# --- 降级调度 ---------------------------------------------------------------

@dataclass
class SearchOutcome:
    """一次检索的结果与它走的是哪条路。界面要如实显示走的哪条。"""

    hits: list[Hit] = field(default_factory=list)
    rung: str = "none"           # binding | provider | whitelist | none
    detail: str = ""
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"hits": [h.to_dict() for h in self.hits], "rung": self.rung,
                "detail": self.detail, "problems": self.problems,
                "urls": [h.url for h in self.hits]}


def search(query: str, *, root: Path, client: SearchClient | None = None,
           provider_search=None, limit: int = DEFAULT_LIMIT) -> SearchOutcome:
    """按可用性降级地找一批候选依据。

    `client` 是用户绑定的搜索服务（第 ① 级）；`provider_search` 是 AI 服务商
    自带的联网能力（第 ② 级，签名 `(query, limit) -> list[Hit]`）。
    两者都没有或都失败时退到白名单站内目录（第 ③ 级）。

    **每一级失败都记进 problems 并继续降级**，不抛异常：
    检索不到不是故障，是要如实告诉用户的事实。
    """
    out = SearchOutcome()

    if client is not None:
        try:
            hits = client.search(query, limit)
            if hits:
                return SearchOutcome(hits=hits, rung="binding",
                                     detail=client.spec.name_zh)
            out.problems.append(f"{client.spec.name_zh}没有返回任何结果")
        except SearchError as exc:
            out.problems.append(f"{client.spec.name_zh}：{exc}")

    if provider_search is not None:
        try:
            hits = provider_search(query, limit)
            if hits:
                return SearchOutcome(hits=hits, rung="provider",
                                     detail="AI 服务商自带联网",
                                     problems=out.problems)
            out.problems.append("AI 服务商的联网检索没有返回结果")
        except Exception as exc:                       # noqa: BLE001
            out.problems.append(f"AI 服务商联网检索失败：{exc}")

    hits = search_whitelist(query, root, limit)
    if hits:
        return SearchOutcome(hits=hits, rung="whitelist",
                             detail="白名单站内目录（未接搜索服务）",
                             problems=out.problems)
    out.problems.append("白名单站内目录里也没有匹配的条目")
    out.rung = "none"
    out.detail = "三条检索路径都没有结果"
    return out


# --- 绑定门面 ---------------------------------------------------------------
#
# 与 `server/ai/__init__.py` 的绑定门面同构，凭据也走同一个系统凭据库。
# **搜索绑定与 AI 绑定完全独立**：解绑搜索服务不动 AI 账号，反之亦然。
# 没绑搜索服务时引导式选型退到白名单站点那一级，其余功能一切照常。

def current_client() -> SearchClient | None:
    """当前生效的搜索服务客户端。没绑定或 key 不在凭据库里就返回 None。"""
    from .ai.credentials import read_key
    from .config import data_dir
    from .ai.credentials import load_account, search_profile

    acc = load_account(data_dir())
    b = acc.get_search()
    if b is None:
        return None
    key = read_key(b.profile or search_profile(b.provider))
    if not key:
        return None
    try:
        return SearchClient(key, spec=get_spec(b.provider))
    except SearchError:
        return None


def bind(api_key: str, *, provider: str = DEFAULT_PROVIDER) -> dict:
    """验证并保存一个搜索服务绑定，绑完即生效。

    **验证会真的发一次查询，消耗一次配额**（三家都没有免费的"验证"接口）。
    这一点必须在点按钮之前就写在对话框里 —— 与方舟/百炼的对话探针同一个处理方式：
    悄悄花掉用户的钱，哪怕只有几分，也不该做。
    """
    from .ai.credentials import (SearchBinding, mask, now_iso,
                                 save_search_binding, search_profile, store_key)
    from .config import data_dir

    spec = get_spec(provider)
    probe = SearchClient(api_key, spec=spec)
    result = probe.validate()

    profile = search_profile(spec.id)
    store_key(api_key, profile)
    b = SearchBinding(profile=profile, provider=spec.id, label=mask(api_key),
                      bound_at=now_iso(), last_hits=int(result.get("hits") or 0))
    save_search_binding(data_dir(), b)
    return {"binding": b.to_dict(), "validation": result,
            "name_zh": spec.name_zh}


def activate(provider: str) -> dict:
    """切换当前生效的搜索服务。不动任何凭据。"""
    from .ai.credentials import (load_account, read_key, save_account,
                                 search_profile)
    from .config import data_dir

    acc = load_account(data_dir())
    if provider not in acc.search_bindings:
        raise SearchError(f"没有绑定过 {get_spec(provider).name_zh}。",
                          kind="not_bound")
    if not read_key(search_profile(provider)):
        raise SearchError(
            f"{get_spec(provider).name_zh}的元数据还在，但系统凭据库里找不到 key —— "
            "可能被其它程序清理过，请重新绑定。", kind="key_missing")
    acc.search_active = provider
    save_account(data_dir(), acc)
    return {"search_active": provider,
            "binding": acc.search_bindings[provider].to_dict()}


def unbind(provider: str | None = None) -> dict:
    """解绑一个搜索服务，删掉它的 key 与元数据。"""
    from .ai.credentials import (clear_search_binding, delete_key, load_account,
                                 search_profile)
    from .config import data_dir

    acc = load_account(data_dir())
    target = provider or acc.search_active
    if not target:
        return {"unbound": False, "key_removed": False, "reason": "本来就没有绑定"}
    removed = delete_key(search_profile(target))
    clear_search_binding(data_dir(), target)
    return {"unbound": True, "provider": target, "key_removed": removed,
            "search_active": load_account(data_dir()).search_active}


def status() -> dict:
    """给设置页用的搜索服务状态。**绝不回显 key 本身。**

    `rung` 说明"现在这台机器上，阶段 1 的检索实际会走哪一级"——
    界面要如实显示，不能让用户以为绑了 AI 就等于能联网检索。
    """
    from .ai.credentials import load_account, read_key, search_profile
    from .config import data_dir

    acc = load_account(data_dir())
    bound = bool(acc.search_bindings) and bool(current_client())
    return {
        "bound": bound,
        "search_active": acc.search_active,
        "bindings": [
            {**b.to_dict(),
             "name_zh": get_spec(b.provider).name_zh,
             "active": pid == acc.search_active,
             "key_present": bool(read_key(search_profile(pid)))}
            for pid, b in acc.search_bindings.items()
        ],
        "providers": catalog(),
        "whitelist": research.whitelist(),
        "rung": "binding" if bound else "whitelist",
    }

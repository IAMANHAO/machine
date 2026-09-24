"""server.research —— 阶段 1 的取证层：服务器自己抓，自己核。

## 这一层存在的理由

上一版让 AI 一次性起草工作流，闸门只有两道（能解析 + 不引用数据表）。
那拦得住"编造的数据表"，拦不住**编造的依据**：模型说"依据 GB/T 1095-2003 表 1"，
没有任何东西去证实这份文件里真有这句话。

所以这里做一件很朴素的事：**AI 声称的依据，服务器自己去抓一遍，
抓回来的正文里必须真的出现它声称的标准号。** 对不上的依据不予采纳。

三条不可绕过的规则：

1. **AI 给的 URL 必须来自服务端传给它的检索结果集**（`keep_known_urls()`）。
   不在集合里的直接剔除——防止它凭记忆编一个看起来很真的链接。
2. **抓回来的正文是资料，不是指令。** 本模块只负责取回与比对，
   喂给模型时由调用方用明确的数据边界包起来。
3. **本层不碰数值。** 它只回答一个问题：这条依据是不是真的存在、
   能不能被第二处独立来源印证。数值的三个合法出身（内置数据表 / 用户手输 /
   引擎算出）与这里无关。

## 印证分档

| 档 | 含义 |
|---|---|
| `cross_checked` | ≥2 个**不同注册域**的文档里都出现了声称的标准号 |
| `trusted` | 只有一处，但那一处在白名单 trusted 档（mechtool.cn） |
| `single_source` | 只有一处，且不在 trusted 档 —— 照实标，**不阻断** |
| `unverified` | 抓到了，但正文里没有声称的标准号 —— **不予采纳** |
| `unverifiable_claim` | 依据的表述里提不出可比对的关键词，只能由用户自己核 |

**`trusted` 的语义是"够资格独自支撑一条依据往下走"，不是"够资格标绿"。**
🟢 仍然只给"两个独立信源相互印证"——那是 `references/source_priority.md`
自己定的规矩。项目的审计里已经记到过 mechtool.cn 与另一处信源给出
两套不同口径的实例（`shaft/material_stress`），把 trusted 直接当绿灯
会把那种分歧悄悄盖掉。

## 抓取的护栏

面向公网发请求的代码必须假定 URL 是不可信的（它来自模型的输出）：

- 只允许 http / https，其余 scheme 一律拒
- **每一跳**都做 DNS 解析并检查 IP：回环 / 私网 / link-local / 保留段一律拒
  （只查最初的主机名是不够的——重定向可以把你送进内网）
- 手动跟随重定向，最多 3 跳；不带任何凭据；限时 8 s；限 2 MB
- 抓取量很小：每条被引用的 URL 一次，结果按会话缓存
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import yaml

TIMEOUT = 8.0
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "MDS-Selector/1.0 (mechanical design selection tool; one page per cited source)"


# --- 白名单站点 -------------------------------------------------------------

@dataclass(frozen=True)
class Site:
    """一个登记过的信源站点。

    `tier`：
      - `trusted` —— 用户明确指定为可独自支撑一条依据的站点
      - `normal`  —— 正常信源，单独一处时仍标 single_source

    `index_urls`：没有关键词检索接口的站点，靠抓这些索引页建本地目录。
    """

    domain: str
    name_zh: str
    tier: str
    index_urls: tuple[str, ...] = ()
    note: str = ""


# 站点表刻意很短。**宁可表里只有一个站，也不要一条打不开的链接。**
# 新增站点前先实测能抓到正文，抓不到的不写进来。
SITES: tuple[Site, ...] = (
    Site(
        domain="mechtool.cn",
        name_zh="机械工具箱 mechtool.cn",
        tier="trusted",
        index_urls=(
            "https://www.mechtool.cn/",
            "https://www.mechtool.cn/formular/index.html",
        ),
        note="用户指定为可信站点。没有关键词搜索接口，靠抓分类索引页建本地目录。",
    ),
    Site(
        domain="openstd.samr.gov.cn",
        name_zh="国家标准全文公开系统",
        tier="normal",
        note="信源优先级第 1 级（国家标准原文）。页面为 JS 渲染，可达性需逐条实测。",
    ),
    Site(
        domain="jlc-jdgf.com",
        name_zh="嘉立创 FA 机械设计手册在线版",
        tier="normal",
        note="成大先《机械设计手册》结构化转载，上一阶段已实测可提取整表。",
    ),
)

_BY_DOMAIN = {s.domain: s for s in SITES}
TRUSTED_DOMAINS = frozenset(s.domain for s in SITES if s.tier == "trusted")


def site_for(host: str) -> Site | None:
    """主机名落在哪个登记站点下。子域算在主站点名下。"""
    h = (host or "").lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    for domain, site in _BY_DOMAIN.items():
        if h == domain or h.endswith("." + domain):
            return site
    return None


def whitelist() -> list[dict]:
    """给设置页/诊断用的站点清单。"""
    return [{"domain": s.domain, "name_zh": s.name_zh, "tier": s.tier,
             "note": s.note, "index_urls": list(s.index_urls)} for s in SITES]


# --- 注册域归并 -------------------------------------------------------------

# "两个不同来源"要按注册域算，不能按主机名算：a.example.com 与 b.example.com
# 是同一家，算成两处印证是自欺。这里用的是一份**近似**的公共后缀表——
# 完整的 PSL 有几千行，为三个站点引入一个依赖不值得。近似的代价是
# 极少数域名会被归并得过粗（宁可过粗：过粗只会少算一处印证，偏保守）。
_TWO_LABEL_SUFFIXES = frozenset("""
com.cn net.cn org.cn gov.cn edu.cn ac.cn mil.cn
co.uk org.uk ac.uk gov.uk
com.hk com.tw com.sg com.au co.jp or.jp ne.jp co.kr
com.br com.mx co.in com.tr
""".split())


def registrable_domain(host: str) -> str:
    h = (host or "").lower().strip(".")
    parts = h.split(".")
    if len(parts) <= 2:
        return h
    if ".".join(parts[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# --- 抓取护栏 ---------------------------------------------------------------

class FetchError(Exception):
    """抓取失败。message 面向用户，kind 便于前端分类。"""

    def __init__(self, message: str, *, kind: str = "fetch_failed"):
        super().__init__(message)
        self.kind = kind


def _reject_ip(ip: str) -> str | None:
    """这个地址能不能抓。返回拒绝理由，None 表示放行。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return f"解析出的地址 {ip!r} 不是合法 IP"
    if (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
        return f"{ip} 指向本机或内网，拒绝抓取"
    return None


def guard_url(url: str) -> str:
    """检查一个 URL 能不能抓，通过则返回规范化后的 URL。

    **每一跳都要过这里**，不只是最初那一个：重定向可以把你送进内网。
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise FetchError(
            f"只允许 http/https，拒绝 {parsed.scheme or '空'} 协议的地址。",
            kind="bad_scheme")
    host = parsed.hostname
    if not host:
        raise FetchError(f"地址里没有主机名：{url!r}", kind="bad_url")
    if parsed.port is not None and parsed.port not in (80, 443):
        raise FetchError(f"只允许 80/443 端口，拒绝 {parsed.port}。", kind="bad_port")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"域名解析不了：{host}", kind="dns") from exc
    for info in infos:
        reason = _reject_ip(info[4][0])
        if reason:
            raise FetchError(reason, kind="blocked_address")
    return parsed.geturl()


# --- 正文抽取 ---------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """极简正文抽取：丢掉 script/style/noscript，收 title 与可见文本。

    用 stdlib 而不是 bs4/lxml —— 这一层只要判断"某个标准号有没有出现在正文里",
    不需要理解文档结构，不值得为它新增一个依赖。
    """

    _DROP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._chunks: list[str] = []
        self._drop_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self._drop_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._DROP and self._drop_depth:
            self._drop_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._drop_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self._chunks)


def extract_text(html: str) -> tuple[str, str]:
    """(正文, 标题)。解析不了的 HTML 按纯文本处理，不抛异常。"""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html, ""
    return parser.text, " ".join(parser.title.split())


class _LinkExtractor(HTMLParser):
    """收 <a href> 与它的锚文本。给"抓索引页建本地目录"用。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            self._href = href
            self._label = []

    def handle_data(self, data):
        if self._href is not None:
            text = data.strip()
            if text:
                self._label.append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            label = " ".join(" ".join(self._label).split())
            if label:
                self.links.append({"href": self._href, "title": label})
            self._href = None
            self._label = []


def extract_links(html: str, base_url: str) -> list[dict]:
    """索引页里的 [{url, title}]，已按 base_url 展开成绝对地址并去重。

    只保留与 base_url **同站**的链接：索引页上的外链不属于这个站点的目录。
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    base_host = urlparse(base_url).hostname or ""
    base_reg = registrable_domain(base_host)
    out: list[dict] = []
    seen: set[str] = set()
    for link in parser.links:
        url = urljoin(base_url, link["href"])
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if registrable_domain(parsed.hostname or "") != base_reg:
            continue
        key = _norm_url(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "title": link["title"]})
    return out


# --- 抓取 -------------------------------------------------------------------

@dataclass
class Doc:
    """抓回来的一篇文档。不存整页 HTML —— 只留正文与指纹。"""

    url: str                 # 请求的那个 URL（AI 引用的那个）
    final_url: str           # 跟随重定向之后的落点
    title: str = ""
    text: str = ""
    fetched_at: str = ""
    fingerprint: str = ""
    status: int = 0
    error: str = ""          # 非空 = 这篇没抓到，理由在此

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)

    @property
    def host(self) -> str:
        return urlparse(self.final_url or self.url).hostname or ""

    @property
    def domain(self) -> str:
        return registrable_domain(self.host)

    @property
    def tier(self) -> str:
        site = site_for(self.host)
        return site.tier if site else "unlisted"

    def to_dict(self, *, with_text: bool = False, excerpt: int = 0) -> dict:
        d = {"url": self.url, "final_url": self.final_url, "title": self.title,
             "host": self.host, "domain": self.domain, "tier": self.tier,
             "fetched_at": self.fetched_at, "fingerprint": self.fingerprint,
             "status": self.status, "ok": self.ok, "error": self.error}
        if with_text:
            d["text"] = self.text
        elif excerpt:
            d["excerpt"] = self.text[:excerpt]
        return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url: str, *, timeout: float = TIMEOUT,
          client: httpx.Client | None = None) -> Doc:
    """抓一篇文档。**抓不到不抛异常**，把理由记在 Doc.error 里。

    调用方要对一批 URL 逐条取证，其中若干条抓不到是常态，不该中断整批。
    只有 URL 本身就该被拒（协议不对、指向内网）才算异常路径——那也记进 error。

    `client` 是可选的既有连接（测试传 MockTransport，也便于将来复用连接）。
    **它不影响任何一道护栏**：`guard_url()` 逐跳照样跑。
    """
    if client is not None:
        return _fetch_with(client, url)
    with httpx.Client(timeout=timeout, follow_redirects=False,
                      headers={"User-Agent": USER_AGENT}) as own:
        return _fetch_with(own, url)


def _fetch_with(client: httpx.Client, url: str) -> Doc:
    doc = Doc(url=url, final_url=url, fetched_at=_now())
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            # 逐跳都过护栏 —— 只查最初那个主机名是不够的，
            # 重定向可以把你从公网送进内网。
            current = guard_url(current)
            with client.stream("GET", current) as resp:
                doc.status = resp.status_code
                if resp.is_redirect:
                    loc = resp.headers.get("location") or ""
                    if not loc:
                        doc.error = f"HTTP {resp.status_code} 但没有 Location 头"
                        return doc
                    current = urljoin(current, loc)
                    continue
                doc.final_url = current
                if resp.status_code != 200:
                    doc.error = f"HTTP {resp.status_code}"
                    return doc
                raw = bytearray()
                for chunk in resp.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        doc.error = f"正文超过 {MAX_BYTES // 1024} KB，已放弃"
                        return doc
                doc.fingerprint = hashlib.sha256(bytes(raw)).hexdigest()[:12]
                charset = resp.charset_encoding or "utf-8"
                try:
                    html = bytes(raw).decode(charset, errors="replace")
                except LookupError:
                    html = bytes(raw).decode("utf-8", errors="replace")
                doc.text, doc.title = extract_text(html)
                if not doc.text.strip():
                    doc.error = "抓到了，但抽不出正文（可能整页由 JS 渲染）"
                return doc
        doc.error = f"重定向超过 {MAX_REDIRECTS} 跳"
        return doc
    except FetchError as exc:
        doc.error = str(exc)
        return doc
    except httpx.RequestError as exc:
        doc.error = f"连不上：{type(exc).__name__}"
        return doc


# --- URL 白名单：AI 只能引用检索结果里的链接 ---------------------------------

def keep_known_urls(cited: list[str], known: list[str]) -> tuple[list[str], list[str]]:
    """把 AI 引用的 URL 过一遍检索结果集。返回 (保留, 剔除)。

    比对按**规范化后的 URL**（去 scheme、去 www.、去尾斜杠、去锚点），
    因为模型常把 http 写成 https、把结尾的斜杠丢掉——那不算编造。
    真正要拦的是一个根本没在结果集里出现过的地址。
    """
    index = {_norm_url(u): u for u in known}
    kept: list[str] = []
    dropped: list[str] = []
    for raw in cited:
        real = index.get(_norm_url(raw))
        if real:
            if real not in kept:
                kept.append(real)
        else:
            dropped.append(raw)
    return kept, dropped


def _norm_url(url: str) -> str:
    p = urlparse((url or "").strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    query = f"?{p.query}" if p.query else ""
    return f"{host}{path}{query}"


# --- 取证 -------------------------------------------------------------------

# 标准号：GB/T 1095-2003、GB 3098.1、ISO 281、JB/T 7511、DIN 471、AGMA 2001…
_STD_RE = re.compile(
    r"(?:GB/T|GB|JB/T|JB|HG/T|YB/T|HB|QC/T|TB/T|CB/T|ISO|DIN|AGMA|JIS|ANSI|ASTM|SAE|EN)"
    r"\s*[\-/]?\s*\d+(?:\.\d+)*(?:\s*[-—–]\s*\d{4})?",
    re.IGNORECASE)

# 书名：《机械设计手册》
_BOOK_RE = re.compile(r"《([^》]{2,40})》")


def _squash(s: str) -> str:
    """归一化到"能比对"的形态：去掉全部空白，破折号统一，大写。

    页面上写 `GB/T1095—2003`、`GB/T 1095-2003`、`GB/T 1095–2003` 是同一件事，
    不归一化会把三种写法判成三份不同的依据。
    """
    s = re.sub(r"\s+", "", s or "")
    s = s.replace("—", "-").replace("–", "-").replace("－", "-")
    return s.upper()


def claim_keys(claim: str) -> list[str]:
    """从依据的表述里提出"必须在正文里出现"的关键词。

    优先标准号（最硬），其次书名。**年份是可选的**：
    页面常只写 `GB/T 1095` 不带年份，要求年份一致会把对的依据判成假的。
    提不出任何关键词时返回空列表 —— 调用方据此标 `unverifiable_claim`，
    交给用户自己核，而不是假装验过了。
    """
    text = claim or ""
    keys: list[str] = []
    for m in _STD_RE.finditer(text):
        core = _squash(m.group(0)).split("-")[0]   # 去掉年份后缀
        if core not in keys:
            keys.append(core)
    if keys:
        return keys
    for m in _BOOK_RE.finditer(text):
        k = _squash(m.group(1))
        if k not in keys:
            keys.append(k)
    return keys


@dataclass
class Evidence:
    """一条依据的取证结果。"""

    claim: str
    keys: list[str] = field(default_factory=list)
    status: str = "unverified"
    hits: list[dict] = field(default_factory=list)      # 印证到的文档
    misses: list[dict] = field(default_factory=list)    # 抓到了但没印证到
    failures: list[dict] = field(default_factory=list)  # 没抓到
    domains: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """够不够资格让用户确认为依据并往下走。

        `unverified` 不行——抓到了正文却没有它声称的标准号，那是最该拦的情况。
        `unverifiable_claim` 行，但界面必须明说"无法自动取证，请自行核对"。
        """
        return self.status in ("cross_checked", "trusted", "single_source",
                               "unverifiable_claim")

    def to_dict(self) -> dict:
        return {"claim": self.claim, "keys": self.keys, "status": self.status,
                "usable": self.usable, "domains": self.domains,
                "hits": self.hits, "misses": self.misses, "failures": self.failures}


def corroborate(claim: str, docs: list[Doc]) -> Evidence:
    """这条依据能不能被抓回来的文档印证。

    判据只有一条：**声称的全部关键词都出现在同一篇文档的正文里。**
    出现在不同文档里不算——那只说明这些词各自存在，不说明这份依据存在。
    """
    keys = claim_keys(claim)
    ev = Evidence(claim=claim, keys=keys)
    if not keys:
        ev.status = "unverifiable_claim"
        ev.failures = [d.to_dict() for d in docs if not d.ok]
        ev.hits = [d.to_dict() for d in docs if d.ok]
        return ev

    for doc in docs:
        if not doc.ok:
            ev.failures.append(doc.to_dict())
            continue
        body = _squash(doc.text)
        if all(k in body for k in keys):
            ev.hits.append(doc.to_dict())
        else:
            ev.misses.append(doc.to_dict())

    ev.domains = sorted({h["domain"] for h in ev.hits if h.get("domain")})
    if len(ev.domains) >= 2:
        ev.status = "cross_checked"
    elif ev.hits:
        ev.status = "trusted" if any(h.get("tier") == "trusted" for h in ev.hits) \
            else "single_source"
    else:
        ev.status = "unverified"
    return ev


def verify_basis(claim: str, urls: list[str], known: list[str],
                 *, fetched: dict[str, Doc] | None = None) -> tuple[Evidence, list[str]]:
    """一条依据的完整取证：过 URL 白名单 → 逐条抓 → 比对。

    `fetched` 是会话级的抓取缓存（同一个 URL 在一次引导里只抓一次）。
    返回 (取证结果, 被剔除的 URL)。
    """
    cache = fetched if fetched is not None else {}
    kept, dropped = keep_known_urls(urls, known)
    docs: list[Doc] = []
    for url in kept:
        doc = cache.get(url)
        if doc is None:
            doc = fetch(url)
            cache[url] = doc
        docs.append(doc)
    return corroborate(claim, docs), dropped


# --- 证据沉淀（SKILL.md 阶段 1 的"缓存复用"） -------------------------------

_BASIS_FILE = "_basis.yaml"


def basis_path(material: str, root: Path | str) -> Path:
    return Path(root) / "knowledge" / "cache" / material / _BASIS_FILE


def save_basis(material: str, basis: dict, root: Path | str) -> Path:
    """把已确认的依据沉淀到用户目录。

    **只存抽取出的结构化依据 + URL + 抓取时间 + 内容指纹，不存整页 HTML。**
    整页落盘既是体积问题也是版权问题；而取证要复现只需要指纹——
    指纹变了就说明那一页改过了，界面据此提示重新取证。

    写的是 `mds.knowledge` 认得的两段式 YAML（frontmatter + 正文），
    所以它会被 `Knowledge` 当成一张普通的表读到，不需要给引擎加新格式。
    """
    path = basis_path(material, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "data_source": basis.get("claim") or "",
        "second_source": "；".join(basis.get("domains") or []),
        "verification_status": _to_confidence(basis.get("status") or ""),
        "last_verified": datetime.now(timezone.utc).date().isoformat(),
        "_todo": (
            "本依据由引导式选型在线取证：服务器抓取了下列 URL，并确认正文中"
            "确实出现了所声称的标准号。**取证证明的是这份文件里有这句话，"
            "不是这个公式适用于你的工况。** 标 🟢 仍需标准原件或纸质手册核对。"),
    }
    body = {"basis": basis}
    text = ("---\n"
            + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            + "---\n\n"
            + yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
    path.write_text(text, encoding="utf-8")
    return path


def _to_confidence(status: str) -> str:
    """取证分档 → 数据表的 verification_status。

    **cross_checked 也只给 single_source。** 两处电子转载是同一份标准的
    两次转写，不构成两个独立信源——这条规矩在引导式这里同样成立，
    不会因为是联网抓的就升绿。
    """
    return "single_source" if status in ("cross_checked", "trusted",
                                         "single_source") else "unknown"

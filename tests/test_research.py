"""取证层 —— 服务器自己抓、自己核。

这一层守的性质只有一条，但它是整个引导式选型的地基：
**AI 声称的依据，必须能被服务器独立取回并比对上；对不上的不予采纳。**

护栏部分（SSRF）要按"URL 是不可信输入"来测——它确实来自模型的输出。
"""

from __future__ import annotations

import httpx
import pytest
import yaml

from server import research as R

# 公网 IP 字面量：getaddrinfo 对字面量不查 DNS，所以这些测试离线也能跑。
PUBLIC = "93.184.216.34"


def _mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler),
                        follow_redirects=False)


def _doc(url: str, text: str, *, ok: bool = True) -> R.Doc:
    return R.Doc(url=url, final_url=url, text=text if ok else "",
                 title="t", fetched_at="2026-09-24T00:00:00+00:00",
                 fingerprint="abc123", status=200 if ok else 500,
                 error="" if ok else "HTTP 500")


# ── 抓取护栏：URL 是不可信输入 ─────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/",
    "javascript:alert(1)",
])
def test_fetch_refuses_non_http_schemes(url):
    with pytest.raises(R.FetchError) as exc:
        R.guard_url(url)
    assert exc.value.kind == "bad_scheme"


@pytest.mark.parametrize("host", [
    "127.0.0.1",        # 回环
    "0.0.0.0",          # unspecified
    "10.0.0.5",         # 私网 A
    "172.16.3.4",       # 私网 B
    "192.168.1.1",      # 私网 C
    "169.254.169.254",  # link-local —— 云上的元数据服务，最该拦的一个
    "[::1]",            # IPv6 回环
])
def test_fetch_refuses_private_and_loopback_addresses(host):
    with pytest.raises(R.FetchError) as exc:
        R.guard_url(f"http://{host}/anything")
    assert exc.value.kind == "blocked_address"


def test_fetch_refuses_odd_ports():
    with pytest.raises(R.FetchError) as exc:
        R.guard_url(f"http://{PUBLIC}:6379/")
    assert exc.value.kind == "bad_port"


def test_redirect_into_private_network_is_blocked():
    """只查最初那个主机名是不够的 —— 重定向可以把你送进内网。"""
    def handler(request):
        if request.url.host == PUBLIC:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, text="<p>内网的秘密</p>")

    doc = R.fetch(f"http://{PUBLIC}/start", client=_mock(handler))
    assert not doc.ok
    assert "内网" in doc.error
    assert "秘密" not in doc.text


def test_redirect_loop_gives_up_instead_of_spinning():
    def handler(request):
        return httpx.Response(302, headers={"location": f"http://{PUBLIC}/next"})

    doc = R.fetch(f"http://{PUBLIC}/a", client=_mock(handler))
    assert not doc.ok
    assert "重定向超过" in doc.error


def test_oversized_body_is_abandoned(monkeypatch):
    monkeypatch.setattr(R, "MAX_BYTES", 64)

    def handler(request):
        return httpx.Response(200, text="x" * 5000)

    doc = R.fetch(f"http://{PUBLIC}/big", client=_mock(handler))
    assert not doc.ok
    assert "已放弃" in doc.error


def test_fetch_keeps_the_body_and_a_fingerprint():
    def handler(request):
        return httpx.Response(
            200, text="<html><head><title>平键</title></head>"
                      "<body><script>x()</script><p>GB/T 1095-2003</p></body></html>")

    doc = R.fetch(f"http://{PUBLIC}/k", client=_mock(handler))
    assert doc.ok
    assert doc.title == "平键"
    assert "GB/T 1095-2003" in doc.text
    assert "x()" not in doc.text          # script 不算正文
    assert len(doc.fingerprint) == 12


def test_non_utf8_page_still_decodes():
    def handler(request):
        return httpx.Response(
            200, content="<p>GB/T 1095 键槽</p>".encode("gbk"),
            headers={"content-type": "text/html; charset=gbk"})

    doc = R.fetch(f"http://{PUBLIC}/gbk", client=_mock(handler))
    assert doc.ok
    assert "键槽" in doc.text


def test_js_only_page_is_reported_as_no_body_not_as_success():
    """抽不出正文要如实说，不能当成"抓到了"——否则取证会拿空正文去比对。"""
    def handler(request):
        return httpx.Response(200, text="<html><body><div id=app></div></body></html>")

    doc = R.fetch(f"http://{PUBLIC}/spa", client=_mock(handler))
    assert not doc.ok
    assert "抽不出正文" in doc.error


# ── URL 白名单：AI 只能引用检索结果里的链接 ────────────────────────

def test_url_not_in_search_results_is_dropped():
    known = ["https://www.mechtool.cn/key.html"]
    kept, dropped = R.keep_known_urls(
        ["https://www.mechtool.cn/key.html",
         "https://totally-made-up.example/gb1095"], known)
    assert kept == known
    assert dropped == ["https://totally-made-up.example/gb1095"]


def test_url_matching_tolerates_scheme_www_and_trailing_slash():
    """把 http 写成 https、丢掉结尾斜杠不算编造，不该因此剔掉一条真链接。"""
    kept, dropped = R.keep_known_urls(
        ["http://mechtool.cn/key.html/"], ["https://www.mechtool.cn/key.html"])
    assert kept == ["https://www.mechtool.cn/key.html"]
    assert dropped == []


# ── 依据关键词的提取 ───────────────────────────────────────────────

@pytest.mark.parametrize("claim,expected", [
    ("GB/T 1095-2003 表 1", ["GB/T1095"]),
    ("依据 GB/T1095 剖面尺寸", ["GB/T1095"]),
    ("GB/T 1095—2003 与 GB/T 1096", ["GB/T1095", "GB/T1096"]),
    ("ISO 281 寿命计算", ["ISO281"]),
    ("成大先《机械设计手册》第3卷", ["机械设计手册"]),
])
def test_claim_keys_are_extracted_without_pinning_the_year(claim, expected):
    assert R.claim_keys(claim) == expected


def test_claim_with_nothing_checkable_is_marked_unverifiable():
    ev = R.corroborate("按经验取值", [_doc("https://a.example/x", "随便什么正文")])
    assert ev.status == "unverifiable_claim"
    # 无法自动取证 ≠ 不能用；但界面必须明说要用户自己核。
    assert ev.usable


# ── 取证分档 ───────────────────────────────────────────────────────

def test_basis_without_the_claimed_standard_in_the_body_is_unverified():
    """最该拦的情况：抓到了正文，里面根本没有它声称的标准号。"""
    ev = R.corroborate("GB/T 1095-2003 表 1", [
        _doc("https://www.mechtool.cn/a", "这页讲的是滚动轴承 GB/T 6391"),
    ])
    assert ev.status == "unverified"
    assert not ev.usable
    assert ev.misses and not ev.hits


def test_all_keys_must_appear_in_the_same_document():
    """两个标准号各自出现在不同页里，不说明这条依据存在。"""
    ev = R.corroborate("GB/T 1095 与 GB/T 1096", [
        _doc("https://a.example/x", "只有 GB/T 1095"),
        _doc("https://b.example/y", "只有 GB/T 1096"),
    ])
    assert ev.status == "unverified"


def test_two_domains_is_cross_checked():
    ev = R.corroborate("GB/T 1095-2003", [
        _doc("https://a.example/x", "GB/T 1095-2003 键的剖面尺寸"),
        _doc("https://b.example/y", "GB/T1095 剖面尺寸表"),
    ])
    assert ev.status == "cross_checked"
    assert ev.domains == ["a.example", "b.example"]


def test_single_domain_corroboration_is_not_cross_checked():
    """同一家的两个子域不是两处印证 —— 算成两处是自欺。"""
    ev = R.corroborate("GB/T 1095", [
        _doc("https://one.example.com/x", "GB/T 1095"),
        _doc("https://two.example.com/y", "GB/T 1095"),
    ])
    assert ev.status == "single_source"
    assert ev.domains == ["example.com"]


def test_mechtool_alone_is_trusted_enough_to_continue():
    """用户指定 mechtool.cn 可信：只有这一处也不该卡住流程。"""
    ev = R.corroborate("GB/T 1095-2003", [
        _doc("https://www.mechtool.cn/key.html", "GB/T 1095-2003 普通型平键"),
    ])
    assert ev.status == "trusted"
    assert ev.usable


def test_an_unlisted_single_source_is_usable_but_labelled():
    ev = R.corroborate("GB/T 1095", [_doc("https://blog.example/x", "GB/T 1095")])
    assert ev.status == "single_source"
    assert ev.usable          # 不阻断
    assert ev.status != "trusted"   # 但也别冒充可信站


def test_trusted_site_does_not_become_a_green_light():
    """trusted 的意思是"够资格往下走"，不是"够资格标绿"。

    绿灯只给"两个独立信源相互印证"。项目自己的审计里已经记到过
    mechtool.cn 与另一处信源两套口径的实例，把 trusted 当绿灯会盖掉那种分歧。
    """
    for status in ("trusted", "cross_checked", "single_source"):
        assert R._to_confidence(status) == "single_source"
    assert R._to_confidence("unverified") == "unknown"


def test_failed_fetches_are_reported_not_silently_ignored():
    ev = R.corroborate("GB/T 1095", [
        _doc("https://dead.example/x", "", ok=False),
        _doc("https://www.mechtool.cn/y", "GB/T 1095"),
    ])
    assert ev.status == "trusted"
    assert len(ev.failures) == 1
    assert ev.failures[0]["url"] == "https://dead.example/x"


# ── 端到端：过白名单 → 抓 → 比对 ───────────────────────────────────

def test_verify_basis_drops_unknown_urls_and_uses_the_cache():
    known = ["https://www.mechtool.cn/key.html"]
    cache = {known[0]: _doc(known[0], "GB/T 1095-2003 普通型平键")}
    ev, dropped = R.verify_basis(
        "GB/T 1095-2003 表 1",
        ["https://www.mechtool.cn/key.html", "https://made-up.example/z"],
        known, fetched=cache)
    assert dropped == ["https://made-up.example/z"]
    assert ev.status == "trusted"


# ── 证据沉淀 ───────────────────────────────────────────────────────

def test_saved_basis_is_readable_by_the_engines_knowledge_loader(tmp_path):
    """沉淀下来的依据要能被 mds.knowledge 当成一张普通表读到。

    否则就得给引擎加一种新格式 —— 那是没必要的新概念。
    """
    basis = {"claim": "GB/T 1095-2003 表 1", "status": "trusted",
             "domains": ["mechtool.cn"],
             "urls": ["https://www.mechtool.cn/key.html"]}
    path = R.save_basis("magnet_plate", basis, tmp_path)
    assert path.exists()

    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    meta, body = docs[0], docs[1]
    assert meta["verification_status"] == "single_source"
    assert meta["data_source"] == "GB/T 1095-2003 表 1"
    assert body["basis"]["urls"] == basis["urls"]
    # 不存整页 HTML：既是体积问题也是版权问题
    # 判据是"落盘的内容里没有页面标记"，不是文件名里没有 html
    raw = path.read_text(encoding="utf-8")
    assert "<" not in raw and ">" not in raw

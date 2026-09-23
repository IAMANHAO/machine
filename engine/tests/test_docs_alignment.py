"""人读文档与可执行规格的一致性测试。

`workflows/<m>.yaml` 是计算真源，`workflows/<m>.md` 是给人看的。
两份文件分开维护就一定会漂——M7 结束时它们已经在好几处对不上，
而 `.md` 恰恰是 skill 仍在读的那一份。

这组测试把"对齐"变成可执行的断言：步骤 id、输入 id、校核项数、
结果行数、被引用的表，逐项比对。改了 YAML 忘了回填 .md，这里会红。
"""

from __future__ import annotations

import dataclasses
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import expr as _expr, knowledge, runner, spec as mds_spec  # noqa: E402

MATERIALS = mds_spec.available()

# runner 里的状态名是模块级常量，不是一个集合
STATUSES = {
    getattr(runner, n) for n in dir(runner)
    if n.isupper() and isinstance(getattr(runner, n), str)
}


def _md(s) -> tuple[str, set[str]]:
    """读人读文档，并取出所有反引号标出的片段。"""
    path = knowledge.Knowledge().root / (s.workflow_doc or f"workflows/{s.material}.md")
    assert path.exists(), f"{s.material} 缺人读文档 {path}"
    text = path.read_text(encoding="utf-8")
    return text, set(re.findall(r"`([^`\n]+)`", text))


def _vocabulary(s) -> set[str]:
    """YAML 侧所有合法标识符：步骤 id、输入 id、枚举值、分档键、表名、采购字段，
    外加规格本身的词汇（步骤类型、状态名、字段名）与被引用表里的真实键。"""
    v = {st.id for st in s.steps} | {i.id for i in s.inputs}

    for i in s.inputs:
        v |= {str(o.get("value")) for o in i.options if isinstance(o, dict)}
        v |= set(i.options_from.values())

    for st in s.steps:
        raw = st.raw
        v |= set(st.outputs)
        # table_pick 对列表字段会自动多产出 <名>_first（推荐往往是区间，
        # 但采购关键词只能带一个值）
        v |= {f"{o}_first" for o in st.outputs}
        for key in ("table", "group", "rows_key", "order_by", "x", "y", "z",
                    "value", "from_input"):
            if isinstance(raw.get(key), str):
                v.add(raw[key])
        for b in raw.get("bins") or []:
            if isinstance(b, dict) and b.get("key"):
                v.add(str(b["key"]))
        for a, b in (raw.get("fields") or {}).items():
            v |= {str(a), str(b)}
        for key in ("key", "path"):
            if isinstance(raw.get(key), str):
                v |= set(re.split(r"[.{}]", raw[key]))

    v |= set((s.procure.get("fields") or {}).keys())
    v |= set(s.procure.get("channels") or [])
    # 结果表与采购关键词的模板里出现的变量名，按定义都是真实变量
    for tpl in list((s.procure.get("fields") or {}).values()) +             [r.get("value", "") for r in s.result]:
        if isinstance(tpl, str):
            v |= set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", tpl))
    v |= set(mds_spec.STEP_KINDS) | STATUSES
    v |= {f.name for f in dataclasses.fields(mds_spec.InputDef)}
    # 表达式白名单里的函数名与常量（.md 讲取整方向时会提到 ceil / floor）
    v |= set(_expr._FUNCS) | set(_expr._CONSTS)
    # 采购关键词模板名（keyword_templates.yaml 里的一段）
    if s.procure.get("material_template"):
        v.add(str(s.procure["material_template"]))
    v |= {"mds", "yaml", "md", "notes", "inputs", "steps", "result", "procure", "when"}

    # .md 摘录数据表结构时会提到表里的键，那些也是真实存在的
    know = knowledge.Knowledge()
    for st in s.steps:
        name = st.raw.get("table")
        if not name:
            continue
        try:
            data = know.table(s.material, name).data
        except Exception:
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                v |= {str(k) for k in cur}
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)

    return {x for x in v if x}


@pytest.mark.parametrize("material", MATERIALS)
def test_every_step_id_appears_in_the_doc(material):
    s = mds_spec.load(material)
    _, ticked = _md(s)
    missing = [st.id for st in s.steps if st.id not in ticked]
    assert not missing, f"{material}.md 没有提到这些步骤：{missing}"


@pytest.mark.parametrize("material", MATERIALS)
def test_every_input_id_appears_in_the_doc(material):
    s = mds_spec.load(material)
    _, ticked = _md(s)
    missing = [i.id for i in s.inputs if i.id not in ticked]
    assert not missing, f"{material}.md 没有提到这些输入：{missing}"


@pytest.mark.parametrize("material", MATERIALS)
def test_doc_mentions_no_identifier_that_does_not_exist(material):
    """.md 里用反引号标出来的小写标识符，必须在 YAML 或数据表里真实存在。

    这条守的是改名之后忘了同步——一个已经不存在的步骤 id 留在文档里，
    比没有文档更误导。"""
    s = mds_spec.load(material)
    _, ticked = _md(s)
    known = _vocabulary(s)
    ghosts = sorted(t for t in ticked
                    if re.fullmatch(r"[a-z][a-z0-9_]{1,30}", t) and t not in known)
    assert not ghosts, f"{material}.md 提到了查无此项的标识符：{ghosts}"


@pytest.mark.parametrize("material", MATERIALS)
def test_doc_counts_match_the_spec(material):
    """文档里写的步骤总数 / 校核项数 / 结果行数必须是真的。"""
    s = mds_spec.load(material)
    text, _ = _md(s)
    cases = [
        (r"共 \*\*(\d+) 个 step\*\*", len(s.steps), "步骤总数"),
        (r"引擎实际执行的 \*\*(\d+) 项\*\* `check`",
         sum(1 for st in s.steps if st.kind == "check"), "校核项数"),
        (r"`yaml: result` 一一对应，共 (\d+) 行", len(s.result), "结果行数"),
    ]
    for pattern, want, what in cases:
        m = re.search(pattern, text)
        assert m, f"{material}.md 没有写{what}"
        assert int(m.group(1)) == want, \
            f"{material}.md 的{what}写的是 {m.group(1)}，实际是 {want}"


@pytest.mark.parametrize("material", MATERIALS)
def test_every_referenced_table_is_listed_in_the_doc(material):
    s = mds_spec.load(material)
    text, _ = _md(s)
    tables = sorted({st.raw["table"] for st in s.steps if st.raw.get("table")})
    missing = [f"{t}.yaml" for t in tables if f"{t}.yaml" not in text]
    assert not missing, f"{material}.md 的数据表清单少了：{missing}"


# --- 数据表本身必须读得出来 ---------------------------------------------

def test_every_cached_table_actually_loads():
    """每张缓存表都要能解析。

    建表时踩过三次同一个坑：双引号嵌在双引号字符串里（_todo 写"某某"），
    YAML 解析直接失败。这类错误只在**跑到那张表**的时候才暴露，
    一个不常走的分支能把它藏很久。这条测试把它挪到最前面。
    """
    know = knowledge.Knowledge()
    broken = []
    for material in know.materials():
        for name in know.tables_of(material):
            try:
                know.table(material, name)
            except Exception as exc:
                broken.append(f"{material}/{name}.yaml: "
                              f"{type(exc).__name__}: {str(exc).splitlines()[0][:60]}")
    assert not broken, "这些缓存表读不出来：" + " | ".join(broken)


def test_every_table_declares_its_source_and_status():
    """没有 data_source 或 verification_status 的表，其数值就是来路不明的。"""
    know = knowledge.Knowledge()
    bad = []
    for material in know.materials():
        for name in know.tables_of(material):
            tbl = know.table(material, name)
            if not tbl.data_source:
                bad.append(f"{material}/{name}.yaml 没有 data_source")
            if tbl.confidence not in ("verified", "single_source", "self_defined"):
                bad.append(f"{material}/{name}.yaml 的 verification_status "
                           f"不合法：{tbl.confidence!r}")
    assert not bad, " | ".join(bad)


def test_no_table_claims_to_be_verified_without_a_second_source():
    """假绿灯比没有绿灯更危险——它会让人以为这个数已经被验证过了。"""
    know = knowledge.Knowledge()
    fake = [f"{m}/{n}.yaml" for m in know.materials() for n in know.tables_of(m)
            if know.table(m, n).confidence == "verified"
            and not know.table(m, n).meta.get("second_source")]
    assert not fake, "标了 verified 却没有第二信源：" + "、".join(fake)

"""引擎测试 —— 重点不在覆盖率，在于守住三条不能破的底线：

1. 黄金用例的每一步都与手算对得上
2. 数据表覆盖不到的工况必须拒绝计算，而不是外推出一个数字
3. 表达式求值不能被用来执行任意代码
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import (  # noqa: E402
    DataMissing, ExprError, Knowledge, NoSolution, SpecError, load_spec, run,
)
from mds import expr as _expr  # noqa: E402
from mds import spec as _spec  # noqa: E402
from mds.rounding import round_to_series  # noqa: E402

# 黄金用例：P=5.5kW，n1=1450 r/min，i=2，a0=400mm，H 型梯形齿同步带
GOLDEN = {
    "P": 5.5, "n1": 1450, "i": 2, "a0": 400,
    "prime_mover": "ac_motor_normal",
    "work_machine": "medium_uniform",
    "hours_per_day": "h_le_10",
    "belt_type": "H",
}


@pytest.fixture(scope="module")
def spec():
    return load_spec("synchronous_belt")


@pytest.fixture(scope="module")
def know():
    return Knowledge()


@pytest.fixture(scope="module")
def golden(spec, know):
    return run(spec, GOLDEN, know)


# --- 1. 黄金用例：逐步与手算对照 -------------------------------------------

# 手算对照表。每一行都是独立用笔算过的，不是把引擎输出抄回来。
HAND_CALC = [
    ("K_A", 1.1, "查 condition_factors：交流电动机(正常) × 中等载荷 × ≤10h"),
    ("Pd", 6.05, "1.1 × 5.5"),
    ("pb", 12.7, "H 型节距"),
    ("z1_min", 20, "H 型 900<n1≤1800 档"),
    ("z1", 20, "未指定，取 z1min"),
    ("d1", 80.8507, "20 × 12.7 / π"),
    ("z2", 40, "round(2 × 20)"),
    ("d2", 161.7014, "40 × 12.7 / π"),
    ("v", 6.1383, "π × 80.8507 × 1450 / 60000"),
    ("L0", 1185.0759, "2×400 + π×242.5521/2 + 80.8507² / 1600"),
    ("Lp", 1219.2, "H 系列中 ≥1185.08 的最小标准节线长"),
    ("a", 417.0620, "400 + (1219.2 − 1185.0759)/2"),
    ("z_belt", 96, "round(1219.2 / 12.7)"),
    ("zm", 9, "ent(20 × (0.5 − 80.8507/(6×417.062)))"),
    ("Kz", 1, "zm=9 ≥ 6"),
    ("P0", 1.766667, "n1=1450 行上 z1 在 18(1.60) 与 24(2.10) 间线性插值到 20"),
    ("bs0", 25.4, "H 型基准带宽"),
    ("bs_req", 74.7794, "25.4 × (6.05 / 1.766667)^(1/1.14)"),
    ("bs", 76.2, "H 系列中 ≥74.78 的最小标准带宽"),
    ("Pr", 6.1812, "(76.2/25.4)^1.14 × 1.766667"),
    ("F1", 985.6096, "6.05 × 1000 / 6.1383"),
]


@pytest.mark.parametrize("name,expected,why", HAND_CALC, ids=[r[0] for r in HAND_CALC])
def test_golden_matches_hand_calculation(golden, name, expected, why):
    assert name in golden.outputs, f"黄金用例未产出 {name}"
    assert golden.outputs[name] == pytest.approx(expected, rel=1e-4), why


def test_golden_passes_all_checks(golden):
    assert golden.status == "ok"
    assert golden.checks, "黄金用例应当有校核项"
    assert all(c["detail"]["passed"] for c in golden.checks)


def test_golden_trace_is_self_explaining(golden):
    """每个计算步骤都要能自证：公式、代入、结果、信源，一个都不能少。"""
    for st in golden.steps:
        if st["kind"] not in ("formula", "table_lookup", "table_interp", "round_to_series"):
            continue
        assert st["formula"], f"{st['id']} 缺公式"
        assert st["substitution"], f"{st['id']} 缺代入式"
        assert st["value"] is not None, f"{st['id']} 缺结果"
        if st["kind"] != "formula":
            assert st["source"].get("confidence"), f"{st['id']} 缺信源置信度"


def test_confidence_reflects_real_frontmatter(golden):
    """置信度必须读自数据表真实的 verification_status，不能是装饰。"""
    assert golden.confidence == "single_source"
    assert any("single_source" in w for w in golden.warnings)
    assert {s["table_file"] for s in golden.sources} >= {
        "condition_factors.yaml", "belt_pitch.yaml", "rated_power.yaml"}


def test_deterministic(spec, know):
    """同输入必须逐位相同——这是"可独立复现"的字面含义。"""
    first = run(spec, GOLDEN, know).outputs
    for _ in range(50):
        assert run(spec, GOLDEN, know).outputs == first


# --- 2. 拒绝外推 -----------------------------------------------------------

def test_refuses_to_extrapolate_beyond_row_range(spec, know):
    """n1=4000 超出 rated_power 的 [1450, 2880]，必须停，不能猜。"""
    trace = run(spec, {**GOLDEN, "n1": 4000}, know)
    assert trace.status == "data_missing"
    step = next(s for s in trace.steps if s["id"] == "P0")
    assert step["status"] == "data_missing"
    assert step["value"] is None, "拒绝计算时绝不能留下一个数字"
    assert "4000" in step["error"]["gap"]


def test_refuses_to_extrapolate_beyond_point_range(spec, know):
    """XXH 型该转速下只有 z1=24 一个点，而 z1min=28 —— 同样必须停。"""
    trace = run(spec, {**GOLDEN, "belt_type": "XXH"}, know)
    assert trace.status == "data_missing"
    step = next(s for s in trace.steps if s["id"] == "P0")
    assert step["status"] == "data_missing"
    assert step["value"] is None


def test_width_beyond_series_is_no_solution_not_missing_data(spec, know):
    """L 型带 5.5kW 所需带宽 ~180mm，超出系列上限 101.6。

    这是一个**选型结论**（该换带型），不是数据缺失。两者的指引完全相反：
    数据缺失要去补知识库，无解要去改设计。混为一谈会把人引到错误的方向。
    """
    trace = run(spec, {**GOLDEN, "belt_type": "L"}, know)
    assert trace.status == "no_solution"
    step = next(s for s in trace.steps if s["id"] == "bs")
    assert step["status"] == "no_solution"
    assert step["value"] is None, "无解时同样不能留下一个数字"

    err = step["error"]
    assert err["error"] == "NoSolution"
    assert err["remedy"], "无解必须给出改设计的方向"
    assert err["limit"] == 101.6 and err["required"] > 101.6
    # 不应该出现"去补数据"这种误导性措辞
    assert "补" not in (err["remedy"] or "")


def test_no_solution_and_data_missing_are_distinct(spec, know):
    """同一个工作流里两种失败必须给出不同的 status，供界面分别措辞。"""
    no_sol = run(spec, {**GOLDEN, "belt_type": "L"}, know)
    missing = run(spec, {**GOLDEN, "n1": 4000}, know)
    assert no_sol.status == "no_solution"
    assert missing.status == "data_missing"
    assert missing.blocker["gap"], "数据缺失必须给出缺口位置"
    assert no_sol.blocker["remedy"], "无解必须给出调整建议"


def test_round_to_series_clamp_vs_error():
    series = [12.7, 25.4, 50.8, 101.6]
    assert round_to_series(60, series)["value"] == 101.6
    clamped = round_to_series(500, series)
    assert clamped["value"] == 101.6 and clamped["exceeded"] == "above"
    with pytest.raises(NoSolution):
        round_to_series(500, series, on_exceed="error")


def test_missing_table_names_the_gap(know):
    with pytest.raises(DataMissing) as exc:
        know.table("synchronous_belt", "does_not_exist")
    assert "does_not_exist" in exc.value.gap


# --- 3. 表达式求值安全 -----------------------------------------------------

@pytest.mark.parametrize("src", [
    '__import__("os").system("echo pwned")',
    "().__class__.__bases__",
    "[i for i in range(3)]",
    'open("/etc/passwd").read()',
    "a.b",
    "lambda: 1",
    "globals()",
    "a[0]",
    '"a" * 10',
])
def test_expression_rejects_dangerous_syntax(src):
    with pytest.raises(ExprError):
        _expr.evaluate(src, {"a": 1})


def test_expression_rejects_unknown_variable():
    with pytest.raises(ExprError):
        _expr.evaluate("nope * 2", {"a": 1})


def test_expression_rejects_division_by_zero():
    with pytest.raises(ExprError):
        _expr.evaluate("a / b", {"a": 1, "b": 0})


def test_expression_allows_engineering_forms():
    env = {"z1": 18, "d1": 54.59, "d2": 109.19, "a": 400, "zm": 4}
    assert _expr.evaluate("ent(z1 * (0.5 - (d2 - d1) / (6 * a)))", env) == 8
    assert _expr.evaluate("1 if zm >= 6 else 1 - 0.2 * (6 - zm)", env) == pytest.approx(0.6)
    assert _expr.evaluate("pi", {}) == pytest.approx(math.pi)


def test_substitution_renders_readable_form():
    assert _expr.substitute("K_A * P", {"K_A": 1.2, "P": 5.5}) == "1.2 × 5.5"


# --- 4. 规格校验 -----------------------------------------------------------

def test_spec_rejects_unknown_step_kind():
    with pytest.raises(SpecError, match="kind"):
        _spec.parse({"material": "x", "steps": [{"id": "s", "kind": "teleport"}]})


def test_spec_rejects_forward_reference():
    """步骤引用了还没算出来的变量，应在加载期报错而不是跑到一半炸。"""
    with pytest.raises(SpecError, match="尚不可用"):
        _spec.parse({
            "material": "x",
            "inputs": [{"id": "a", "required": True}],
            "steps": [{"id": "s", "kind": "formula", "expr": "a * later"},
                      {"id": "later", "kind": "formula", "expr": "a"}],
        })


def test_spec_rejects_duplicate_step_id():
    with pytest.raises(SpecError, match="重复"):
        _spec.parse({
            "material": "x",
            "inputs": [{"id": "a"}],
            "steps": [{"id": "s", "kind": "formula", "expr": "a"},
                      {"id": "s", "kind": "formula", "expr": "a"}],
        })


# --- 5. 输入校验与决策 -----------------------------------------------------

def test_out_of_domain_input_is_rejected(spec, know):
    from mds import InputError
    with pytest.raises(InputError, match="上限"):
        run(spec, {**GOLDEN, "P": 9999}, know)


def test_one_of_group_requires_one_member(spec, know):
    from mds import InputError
    values = {k: v for k, v in GOLDEN.items() if k != "i"}
    with pytest.raises(InputError, match="传动比"):
        run(spec, values, know)


def test_n2_can_substitute_for_i(spec, know):
    values = {k: v for k, v in GOLDEN.items() if k != "i"}
    trace = run(spec, {**values, "n2": 725}, know)
    assert trace.outputs["i"] == pytest.approx(2.0)


def test_select_step_halts_instead_of_guessing(spec, know):
    """带型选择图不在缓存里，引擎必须问，不能替用户挑。"""
    values = {k: v for k, v in GOLDEN.items() if k != "belt_type"}
    trace = run(spec, values, know)
    assert trace.status == "needs_choice"
    assert trace.blocker["step"] == "belt_type_sel"
    assert {c["value"] for c in trace.blocker["candidates"]} == {"XL", "L", "H", "XH", "XXH"}


def test_choice_can_be_supplied_separately(spec, know):
    values = {k: v for k, v in GOLDEN.items() if k != "belt_type"}
    trace = run(spec, values, know, choices={"belt_type_sel": "H"})
    assert trace.status == "ok"
    assert trace.outputs["belt_type"] == "H"


def test_skipped_checks_are_not_reported_as_failures(spec, know):
    """把"未执行"显示成"不通过"是危险的误导。"""
    trace = run(spec, {**GOLDEN, "n1": 4000}, know)
    assert all(c["status"] != "skipped" for c in trace.checks)
    skipped = [s for s in trace.steps if s["kind"] == "check" and s["status"] == "skipped"]
    assert skipped, "本用例应当有因中断而未执行的校核"


# --- 6. 两段式 YAML ---------------------------------------------------------

def test_dual_document_yaml_is_merged(know):
    """缓存文件是 frontmatter + 正文两个 YAML 文档，safe_load 会炸，必须用 safe_load_all。"""
    tbl = know.table("synchronous_belt", "belt_pitch")
    assert "belt_types" in tbl.data          # 正文
    assert tbl.data_source.startswith("GB/T")  # frontmatter
    assert tbl.confidence == "single_source"
    assert "data_source" not in tbl.data, "元数据不应混进正文"


def test_every_cached_table_loads(know):
    for material in know.materials():
        for name in know.tables_of(material):
            tbl = know.table(material, name)
            assert tbl.data, f"{material}/{name} 正文为空"
            assert tbl.confidence in ("verified", "single_source", "self_defined", "unknown")


# ── select 的候选写法与"叫法对不上"的归类 ──────────────────────────

def _grade_spec():
    """内联候选用 {value, label} 写法 —— 与枚举输入的 options 同一套写法。"""
    return _spec.parse({
        "material": "magnet_probe", "name_zh": "磁吸铁片",
        "inputs": [{"id": "grade", "name_zh": "磁铁材料牌号", "type": "text",
                    "required": True}],
        "steps": [{"id": "pick", "kind": "select", "name_zh": "选定磁铁材料牌号",
                   "from_input": "grade",
                   "candidates": [
                       {"value": "烧结钕铁硼", "label": "烧结钕铁硼（高磁能积）"},
                       {"value": "铁氧体", "label": "铁氧体（低成本）"}],
                   "reason": "牌号要工程师定", "outputs": ["grade_pick"]}],
        "result": [{"label": "牌号", "value": "{grade_pick}", "unit": ""}],
    })


def test_inline_candidates_accept_the_value_label_form():
    """候选写成 {value, label} 时，value 就是 value。

    早先这里用 str(c) 把整个字典字符串化，于是用户**选了候选里明明有的值**
    也永远对不上，还被报成"该工况点没有数据，请去知识库补表"。
    枚举输入的 options 一直认这个写法，两处必须一致。
    """
    trace = run(_grade_spec(), {"grade": "烧结钕铁硼"}, Knowledge(), {})
    assert trace.status == "ok"
    assert trace.result[0]["value"] == "烧结钕铁硼"
    labels = [c["label"] for c in trace.steps[0]["detail"]["candidates"]]
    assert labels == ["烧结钕铁硼（高磁能积）", "铁氧体（低成本）"]


def test_a_name_that_does_not_match_is_a_choice_problem_not_a_data_gap():
    """叫法对不上 ≠ 数据缺口。

    报成 data_missing 会让界面说"去知识库补这张表"——指向一个不存在的问题。
    正确的归类是"还需要决策"：把候选摆出来让人重选，流程不中断。
    """
    trace = run(_grade_spec(), {"grade": "other"}, Knowledge(), {})
    assert trace.status == "needs_choice"
    assert trace.blocker["error"] != "DataMissing"
    assert "重新选" in trace.blocker["message"]
    # 候选要带过去，否则界面没东西可摆
    assert len(trace.blocker["candidates"]) == 2

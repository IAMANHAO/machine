"""润滑油工作流测试（M7）—— 分支型工作流的压力测试。

同步带是线性流程：一串步骤从头跑到尾。润滑油不是：

- 三类对象（链条 / 开式齿轮 / 导轨）的参数、公式、决策表完全不同
- 表返回的不是一个数，而是一组推荐（VG 候选、基础油、添加剂、典型产品）
- 每条分支产出的字段都不一样，结果表得跟着变

这组测试守的就是这些差异有没有被如实表达，而不是被抹平成"看起来能跑"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import DataMissing, InputError, Knowledge, load_spec, run  # noqa: E402
from mds import expr as _expr  # noqa: E402
from mds.audit import audit_material  # noqa: E402

CHAIN = {"target": "chain", "T_work": 55, "z1": 19, "p_chain": 12.7,
         "n_chain": 300, "chain_method": "hand"}
GEAR = {"target": "open_gear", "T_work": 55, "d_gear": 3000, "n_gear": 15}
RAIL = {"target": "rail", "T_work": 25, "n_screw": 1500, "lead": 10,
        "rail_pair": "metal_metal"}


@pytest.fixture(scope="module")
def spec():
    return load_spec("lubricant")


@pytest.fixture(scope="module")
def know():
    return Knowledge()


# --- 三条分支各自跑通 ------------------------------------------------------

def test_chain_branch(spec, know):
    t = run(spec, CHAIN, know)
    assert t.status == "ok"
    # v = 19 × 12.7 × 300 / 60000
    assert t.outputs["v_chain"] == pytest.approx(1.2065, rel=1e-4)
    assert t.outputs["chain_speed"] == "low_speed"
    assert t.outputs["iso_vg"] == [150, 220, 320]
    assert t.outputs["target_label"] == "链条"


def test_open_gear_branch(spec, know):
    t = run(spec, GEAR, know)
    assert t.status == "ok"
    # v = π × 3000 × 15 / 60000
    assert t.outputs["v_gear"] == pytest.approx(2.3562, rel=1e-4)
    assert t.outputs["gear_speed"] == "medium"
    assert t.outputs["iso_vg"] == [220, 320]
    assert "半流体脂" in t.outputs["form"]


def test_rail_branch(spec, know):
    t = run(spec, RAIL, know)
    assert t.status == "ok"
    # v = 1500 × 10 / 1000 = 15 m/min
    assert t.outputs["v_rail"] == pytest.approx(15.0)
    assert t.outputs["iso_vg"] == [46, 68]
    assert "Vactra" in " ".join(t.outputs["products"])


# --- 分支隔离：不该跑的步骤必须标"不适用"而不是"未执行" --------------------

def test_other_branches_are_not_applicable_not_skipped(spec, know):
    """not_applicable 与 skipped 混为一谈，会让人以为流程出错了。"""
    t = run(spec, CHAIN, know)
    by_id = {s["id"]: s for s in t.steps}

    assert by_id["v_gear"]["status"] == "not_applicable"
    assert by_id["v_rail"]["status"] == "not_applicable"
    assert by_id["gear_rec"]["status"] == "not_applicable"
    # 整条流程是成功的，没有任何一步是"因中断未执行"
    assert all(s["status"] != "skipped" for s in t.steps)
    assert t.status == "ok"


def test_inapplicable_checks_do_not_count(spec, know):
    """开式齿轮专属的校核，在链条分支里不该出现在 4/4 这种计数里。"""
    chain = run(spec, CHAIN, know)
    gear = run(spec, GEAR, know)
    assert {c["id"] for c in chain.checks} == {"chk_mineral_temp", "chk_low_temp"}
    assert "chk_gear_open" in {c["id"] for c in gear.checks}
    assert "chk_gear_open" not in {c["id"] for c in chain.checks}


def test_result_table_only_shows_this_branch_fields(spec, know):
    """结果表列了所有分支可能产出的字段；没走到的分支那几行应当消失，

    而不是把 "{form}" 原样打在报告里。
    """
    chain = {r["label"]: r["value"] for r in run(spec, CHAIN, know).result}
    rail = {r["label"]: r["value"] for r in run(spec, RAIL, know).result}

    assert "推荐形态" not in chain, "链条分支产不出 form，这一行不该出现"
    assert "典型产品" in rail and "典型产品" not in chain
    assert all("{" not in v for v in chain.values()), "不能留下未解析的占位符"
    assert all("{" not in v for v in rail.values())


# --- 条件必填 --------------------------------------------------------------

def test_required_when_only_asks_for_this_branch(spec, know):
    """选了导轨就不该被要求填链条的齿数和节距。"""
    with pytest.raises(InputError) as exc:
        run(spec, {"target": "rail", "T_work": 25}, know)
    msg = str(exc.value)
    assert "导程" in msg and "丝杠" in msg
    assert "链轮" not in msg and "节距" not in msg


def test_missing_common_input_is_still_required(spec, know):
    with pytest.raises(InputError, match="工作温度"):
        run(spec, {k: v for k, v in CHAIN.items() if k != "T_work"}, know)


# --- 决策表：返回一组推荐而不是一个数 --------------------------------------

def test_table_pick_returns_a_recommendation_bundle(spec, know):
    t = run(spec, CHAIN, know)
    step = next(s for s in t.steps if s["id"] == "chain_rec")
    assert step["status"] == "ok"
    assert set(step["outputs"]) >= {"iso_vg", "base_oil", "additives", "rec_notes"}
    # 列表字段额外产出首值，供采购关键词这类只能带一个值的场合
    assert step["outputs"]["iso_vg_first"] == 150
    assert step["source"]["confidence"] == "single_source"


def test_uncovered_combination_reports_missing_data(spec, know):
    """低速 + 油浴表里没有。不能硬凑一个最接近的档蒙混过去。"""
    t = run(spec, {**CHAIN, "chain_method": "oil_bath"}, know)
    assert t.status == "data_missing"
    step = next(s for s in t.steps if s["id"] == "chain_rec")
    assert step["value"] is None
    assert "low_speed_oil_bath" in step["error"]["message"]
    assert step["error"]["available"], "要告诉用户表里到底有哪些组合"


# --- 校核 ------------------------------------------------------------------

def test_open_gear_speed_limit_is_enforced(spec, know):
    """节圆速 > 15 m/s 时开式结构不适用，必须报不通过并给出方向。"""
    t = run(spec, {**GEAR, "n_gear": 120}, know)
    assert t.status == "check_failed"
    chk = next(c for c in t.checks if c["id"] == "chk_gear_open")
    assert chk["detail"]["passed"] is False
    assert "闭式" in chk["detail"]["remedy"]


def test_high_temperature_triggers_synthetic_oil_advice(spec, know):
    t = run(spec, {**CHAIN, "T_work": 120}, know)
    chk = next(c for c in t.checks if c["id"] == "chk_mineral_temp")
    assert chk["detail"]["passed"] is False
    assert "合成" in chk["detail"]["remedy"]


# --- 采购 ------------------------------------------------------------------

@pytest.mark.parametrize("values,kind,vg", [
    (CHAIN, "链条油", "VG150"),
    (GEAR, "开式齿轮脂", "VG220"),
    (RAIL, "导轨油", "VG46"),
])
def test_each_branch_produces_its_own_procurement_keyword(spec, know, values, kind, vg):
    from mds import procure

    t = run(spec, values, know)
    out = procure.from_spec(spec, t.outputs)
    assert kind in out["keyword"]
    assert vg in out["keyword"]
    assert out["links"]


# --- 探测网格 --------------------------------------------------------------

def test_audit_probe_covers_all_branches(know):
    """分支型工作流的探测必须逐条列举；base+sweep 的笛卡尔积在这里表达不了。"""
    rep = audit_material("lubricant", know)
    matrix = rep["probe"]["matrix"]
    assert len(matrix) == 11
    assert all("input_error" != m["status"] for m in matrix), \
        "探测用例本身不该缺参数"

    by_status = {}
    for m in matrix:
        by_status.setdefault(m["status"], []).append(m["combo"]["case"])
    assert len(by_status["ok"]) == 9
    assert by_status["data_missing"] == ["链条·低速油浴（表内无此组合）"]
    assert by_status["check_failed"] == ["开式齿轮·超速（应判定开式不适用）"]


# --- 表达式：字符串只能判等 ------------------------------------------------

def test_branch_guards_can_compare_enums():
    assert _expr.evaluate("target == 'chain'", {"target": "chain"}) is True
    assert _expr.evaluate("t == 'rail' and p == 'vertical'",
                          {"t": "rail", "p": "vertical"}) is True


@pytest.mark.parametrize("src", ['"a" * 10', "'a' + 'b'", "a < 'b'"])
def test_strings_cannot_do_arithmetic_or_ordering(src):
    """放开字符串是为了分支判等，不能顺带放开字符串运算。"""
    from mds import ExprError

    with pytest.raises(ExprError):
        _expr.evaluate(src, {"a": "x"})

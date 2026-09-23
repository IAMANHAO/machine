"""圆柱螺旋压缩弹簧工作流测试。

这个物料最能说明"数据拿不到时该怎么办"：
弹簧钢丝直径系列、切变模量 G、I/II 类许用应力比——三样都检索不到可核对的出处。

处理方式不是编，而是改变分工：
  · 钢丝直径   → 用户填实际能买到的，引擎算出最小值并**校核**
  · G          → 必填输入，从材料证书抄
  · I/II 类系数 → 表里留空，选到就报缺口；另给一条「手工指定 [τ]」的通路

结果是这个工作流"算"的部分很扎实（曲度系数、强度、刚度、圈数全是定义式），
"查"的部分极少。测试要守住的正是这个分工别被后来人悄悄改掉。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import Knowledge, load_spec, run  # noqa: E402

SPEC = load_spec("spring")
KNOW = Knowledge()

# 黄金算例：Fmax 200 N，行程 20 mm，φ4 钢丝，C=6，G=79000，
#           III 类静载，σb=1400 → [τ] = 700 MPa
#   K   = (4×6−1)/(4×6−4) + 0.615/6 = 23/20 + 0.1025 = 1.2525
#   D2  = 6 × 4 = 24 mm
#   d_min = √(8 × 1.2525 × 200 × 6 / (π × 700)) = 2.3384 mm ≤ 4 ✓
#   τ实 = 8 × 1.2525 × 200 × 24 / (π × 4³)      = 239.25 MPa ≤ 700 ✓
#   n   = 79000 × 4⁴ × 20 / (8 × 24³ × 200)
#       = 404480000 / 22118400 = 18.288 → 圆整 18.5 圈
GOLDEN = {
    "F_max": 200, "F_min": 0, "stroke": 20, "d": 4, "C": 6, "G": 79000,
    "tau_source": "from_class", "load_class": "class_3", "sigma_b": 1400,
    "H0": 50, "end_support": "one_fixed_one_free",
}

K_GOLDEN = (4 * 6 - 1) / (4 * 6 - 4) + 0.615 / 6


def _run(**over):
    return run(SPEC, {**GOLDEN, **over}, KNOW)


# --- 黄金算例 -----------------------------------------------------------

def test_golden_case_matches_hand_calculation():
    out = _run().outputs
    assert out["K"] == pytest.approx(K_GOLDEN)
    assert out["tau_allow"] == pytest.approx(0.5 * 1400)
    assert out["D2"] == 24
    assert out["D_out"] == 28
    assert out["d_min"] == pytest.approx(
        math.sqrt(8 * K_GOLDEN * 200 * 6 / (math.pi * 700)))
    assert out["tau_actual"] == pytest.approx(
        8 * K_GOLDEN * 200 * 24 / (math.pi * 4 ** 3))
    assert out["n"] == 18.5


def test_golden_case_passes_every_check():
    trace = _run()
    assert trace.status == "ok"
    assert len(trace.checks) == 4
    assert all(c["detail"]["passed"] for c in trace.checks)


def test_stiffness_is_recomputed_from_the_rounded_turn_count():
    """圈数圆整到 0.5 之后，刚度必须**用圆整值反算**——
    否则报告里的刚度是一个造不出来的弹簧的刚度。"""
    out = _run().outputs
    assert out["k_spring"] == pytest.approx(
        79000 * 4 ** 4 / (8 * 24 ** 3 * out["n"]))
    assert out["f_max"] == pytest.approx(200 / out["k_spring"])


def test_wahl_factor_is_a_definition_not_a_lookup():
    """曲度系数是定义式，不是查表值。换个 C 必须跟着变。"""
    for C in (4, 5, 6, 8, 10, 16):
        want = (4 * C - 1) / (4 * C - 4) + 0.615 / C
        assert _run(C=C, d=8).outputs["K"] == pytest.approx(want)


# --- 数据拿不到时的分工 ---------------------------------------------------

def test_class_1_and_2_report_a_precise_gap_instead_of_guessing():
    """I 类与 II 类的许用应力比常被引作 0.3 和 0.4，但没检索到可核对的出处。
    表里刻意留空——引擎必须报出缺口位置，不能顺手填一个流传很广的数。"""
    for load_class in ("class_1", "class_2"):
        trace = _run(load_class=load_class)
        assert trace.status == "data_missing", load_class
        assert "tau_ratio" in trace.blocker["path"]
        assert load_class in trace.blocker["gap"]


def test_manual_tau_provides_a_way_through_the_gap():
    """留空不等于把用户堵死：手上有材料标准的人可以直接指定 [τ]。
    这条通路必须真的绕开那张缺数据的表。"""
    trace = _run(tau_source="manual", tau_in=700)
    assert trace.status == "ok"
    assert trace.outputs["tau_allow"] == 700
    skipped = [s["name_zh"] for s in trace.steps if s["status"] == "not_applicable"]
    assert "许用切应力比" in skipped


def test_manual_and_class_paths_agree_when_given_the_same_number():
    """两条通路算出的是同一个弹簧——[τ] 一样，后面就该一模一样。"""
    by_class = _run().outputs
    manual = _run(tau_source="manual", tau_in=700).outputs
    for key in ("d_min", "tau_actual", "n", "k_spring", "b_ratio"):
        assert by_class[key] == pytest.approx(manual[key]), key


def test_wire_diameter_is_checked_not_chosen():
    """GB/T 1358 的钢丝直径系列检索不到可核对的数值，所以引擎不做圆整。
    它算出所需最小直径，再校核用户填的实际直径够不够。"""
    ok = _run(d=4)
    thin = _run(d=2)
    assert ok.status == "ok"
    assert thin.status == "check_failed"
    failed = [c["name_zh"] for c in thin.checks if not c["detail"]["passed"]]
    assert "钢丝直径校核" in failed
    # 两次算出的 d_min 必须一样——它只取决于载荷与 C，与填的直径无关
    assert ok.outputs["d_min"] == pytest.approx(thin.outputs["d_min"])


def test_shear_modulus_is_an_input_with_no_default_table():
    """G 是必填输入，不是查表来的——缓存里没有任何 G 值。
    这条守的是后来人"顺手"补一张 G 表却不写出处。"""
    table = KNOW.table("spring", "spring_design").data
    flat = str(table)
    assert "79000" not in flat and "G" not in table
    assert any(i.id == "G" and i.required for i in SPEC.inputs)


# --- 稳定性 -------------------------------------------------------------

def test_stability_limit_depends_on_end_support():
    """端部约束越强，允许的高径比越大。"""
    limits = {}
    for support in ("both_free", "one_fixed_one_free", "both_fixed"):
        limits[support] = _run(end_support=support).outputs["b_limit"]
    assert limits["both_free"] < limits["one_fixed_one_free"] < limits["both_fixed"]


def test_tall_spring_fails_stability_and_is_told_to_add_a_guide():
    """高径比超限的对策是加导杆导套，不是硬改弹簧参数——
    提示词写错会把人引去改一个本来没问题的设计。"""
    trace = _run(H0=200, end_support="both_free")
    failed = [c for c in trace.checks if not c["detail"]["passed"]]
    stability = [c for c in failed if c["name_zh"] == "稳定性校核"]
    assert stability, "H0/D2 = 8.3 远超两端回转的 2.6，应当不通过"
    assert "导杆" in stability[0]["detail"]["remedy"]


def test_result_table_has_no_unrendered_placeholder():
    for row in _run().result:
        assert "{" not in str(row["value"]), row

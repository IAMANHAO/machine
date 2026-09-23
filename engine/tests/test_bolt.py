"""螺栓工作流测试。

这是第一个用 row_select 的物料，也是第一个把"表值可以独立复算"当成
验证手段的物料。三件事值得单独守：

1. **黄金算例逐步与手算对得上**（期望值是笔算出来的，不是抄引擎输出）
2. **表值与定义式一致** —— 螺纹的 d2 / d1 / As 都有几何定义式，
   逐行复算能抓出转写错误。检索时就是这么发现一处信源把 M30 的 As
   抄成 581（正确值 560.6）的。
3. **选不到规格时必须分清是"系列内无解"还是"表缺数据"** ——
   前者该换设计，后者该补表，方向完全相反。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import Knowledge, load_spec, run  # noqa: E402

MATERIAL = "bolt"

# 黄金算例：总拉力 20 kN，单颗 8.8 级碳素钢螺栓，不控制预紧力，假定 M16~M30 档
GOLDEN = {
    "F0_total": 20000, "z": 1, "grade": "c8_8",
    "material_class": "carbon_steel",
    "preload_control": "uncontrolled", "d_band": "d_16_to_30",
    "surface": "general_dry",
}

# 手算对照（每一行都是独立笔算的，改公式要重新手算，别直接改这里）
#   σs = 640 MPa（8.8 级：800 × 0.8）
#   S  = 3.5（碳素钢 M16~M30 不控制预紧力）
#   [σ] = 640 / 3.5                = 182.857 MPa
#   As_req = 1.3 × 20000 / 182.857 = 142.19 mm²
#   → 选 M16（As = 156.7 ≥ 142.19，是最小满足的一档）
#   σ实 = 1.3 × 20000 / 156.7      = 165.92 MPa ≤ 182.857 ✓
#   F0  = 0.6 × 640 × 156.7        = 60172.8 N
#   T   = (0.18~0.21) × 60172.8 × 16 / 1000 = 173.30 ~ 202.18 N·m
HAND_CALC = {
    "sigma_s": 640.0,
    "S": 3.5,
    "sigma_allow": 640 / 3.5,
    "As_req": 1.3 * 20000 / (640 / 3.5),
    "As_actual": 156.7,
    "d_bolt": 16.0,
    "sigma_actual": 1.3 * 20000 / 156.7,
    "F_pre": 0.6 * 640 * 156.7,
    "T_min": 0.18 * (0.6 * 640 * 156.7) * 16 / 1000,
    "T_max": 0.21 * (0.6 * 640 * 156.7) * 16 / 1000,
}


SPEC = load_spec(MATERIAL)
KNOW = Knowledge()


def _run(**overrides):
    values = dict(GOLDEN)
    values.update(overrides)
    return run(SPEC, values, KNOW)


def _rows():
    return KNOW.table(MATERIAL, "thread_series").data["coarse"]["rows"]


# --- 黄金算例 -----------------------------------------------------------

def test_golden_case_matches_hand_calculation():
    trace = _run()
    assert trace.status == "ok"
    for name, want in HAND_CALC.items():
        assert trace.outputs[name] == pytest.approx(want, rel=1e-6), name


def test_golden_case_selects_M16():
    trace = _run()
    assert trace.outputs["size_label"] == "M16"
    assert trace.outputs["grade_code"] == "8.8"


def test_all_checks_pass_on_the_golden_case():
    trace = _run()
    assert [c for c in trace.checks if not c["detail"]["passed"]] == []
    assert len(trace.checks) == 3


def test_run_is_deterministic():
    first = _run()
    for _ in range(20):
        assert _run().result == first.result


# --- 表值与定义式一致（比"两处转载一致"更强的证据）-------------------------

def test_stress_area_matches_the_defining_formula():
    """As = 0.7854 (d − 0.9382 P)²

    检索时正是这条式子判定了「M30 的 As 是 560.6 还是 581」——
    一处转载抄成了 581，差 3.6%，公式一算就露馅。"""
    for row in _rows():
        want = 0.7854 * (row["d"] - 0.9382 * row["P"]) ** 2
        assert row["As"] == pytest.approx(want, rel=5e-3), row["label"]


def test_pitch_diameter_matches_the_defining_formula():
    """d2 = d − 0.6495 P"""
    for row in _rows():
        if "d2" not in row:
            continue
        assert row["d2"] == pytest.approx(row["d"] - 0.6495 * row["P"], abs=1e-3), \
            row["label"]


def test_minor_diameter_matches_the_defining_formula():
    """d1 = d − 1.0825 P"""
    for row in _rows():
        if "d1" not in row:
            continue
        assert row["d1"] == pytest.approx(row["d"] - 1.0825 * row["P"], abs=1e-3), \
            row["label"]


def test_property_classes_match_the_designation_rule():
    """GB/T 3098.1 的等级代号就是数据本身：
    代号 x.y → Rm = x × 100，σs = Rm × y / 10。
    这张表不是抄来的一组数，是规则的展开，所以必须能反推回去。"""
    classes = KNOW.table(MATERIAL, "property_class").data["classes"]
    for key, item in classes.items():
        left, right = item["code"].split(".")
        assert item["Rm"] == float(left) * 100, key
        assert item["sigma_s"] == pytest.approx(item["Rm"] * float(right) / 10), key


def test_thread_series_is_sorted_by_stress_area():
    """row_select 按 As 取最小满足的一行，前提是这一列单调。
    插错行的表会让选型悄悄选大一档。"""
    areas = [r["As"] for r in _rows()]
    assert areas == sorted(areas)


# --- 无解 vs 缺数据：两者方向相反，不能混 ---------------------------------

def test_load_beyond_the_largest_size_is_no_solution_not_missing_data():
    """所需截面积超出 M64，是"这个系列扛不住"，该换设计——
    不是"表里缺数据"，不该让用户去补录。"""
    trace = _run(F0_total=5_000_000, preload_control="controlled")
    assert trace.status == "no_solution"
    assert trace.blocker["error"] == "NoSolution"
    assert trace.blocker["limit"] == pytest.approx(2676.0)
    assert trace.blocker["remedy"]
    # 关键：不能把它说成缺数据，否则会把人引去补表
    assert "gap" not in trace.blocker or not trace.blocker.get("gap")


def test_alloy_steel_uncontrolled_reports_a_precise_data_gap():
    """合金钢 + 不控制预紧力这一档信源没给按直径分的值，表里刻意留空。
    引擎必须报出缺口位置，而不是退到碳素钢那一行凑合。"""
    trace = _run(material_class="alloy_steel", grade="c10_9")
    assert trace.status == "data_missing"
    assert trace.blocker["error"] == "DataMissing"
    assert trace.blocker["table"] == "bolt/safety_factor"
    assert "d_16_to_30" in trace.blocker["gap"]


def test_engine_does_not_fall_back_to_another_material_row():
    """上一条的反面：确认它真的没有悄悄用碳素钢的 S 算出一个结果。"""
    trace = _run(material_class="alloy_steel")
    assert trace.status == "data_missing"
    assert "S" not in trace.outputs


# --- 假定直径档的显式迭代 -------------------------------------------------

def test_wrong_assumed_band_fails_the_check_instead_of_being_silently_fixed():
    """安全系数按直径分档，而直径要算完才知道。本工作流把这个迭代摆到明面上：
    假定档与算出的规格不符时校核不通过，并告诉你改选哪一档——
    而不是在后台悄悄重算一遍。"""
    trace = _run(F0_total=200000, d_band="d_le_16")
    failed = [c for c in trace.checks if not c["detail"]["passed"]]
    assert failed, "假定 M6~M16 却算出大规格，应当校核不通过"
    assert any("直径档" in c["name_zh"] for c in failed)
    assert trace.status == "check_failed"


def test_controlled_preload_does_not_need_the_band_input():
    """控制预紧力时 S 与直径无关，不该逼用户去选一个用不上的档位。"""
    values = {k: v for k, v in GOLDEN.items() if k != "d_band"}
    values["preload_control"] = "controlled"
    trace = run(SPEC, values, KNOW)
    assert trace.status == "ok"
    skipped = [s for s in trace.steps if s["status"] == "not_applicable"]
    assert any("假定档" in s["name_zh"] for s in skipped)


# --- 输出的诚实性 ---------------------------------------------------------

def test_tightening_torque_is_reported_as_a_range():
    """K 随表面状态、润滑、垫圈变化，同一表面处理不同信源能差近一倍。
    把它压成一个两位小数的单值，是用精度冒充准确。"""
    trace = _run()
    assert trace.outputs["T_min"] < trace.outputs["T_max"]
    assert "~" in trace.outputs["T_label"]


def test_result_table_has_no_unrendered_placeholder():
    trace = _run()
    for row in trace.result:
        assert "{" not in str(row["value"]), row

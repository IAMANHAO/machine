"""轴承 / 联轴器 / 减速器 —— 三个"样本主导"物料的工作流测试。

这三个物料共用一条设计原则：**额定值由用户从厂商样本填，引擎只做算术与校核。**

这不是偷懒，是这个产品唯一诚实的做法：
  · 轴承的 C、X、Y 印在样本同一页，抄它们比抄一张来路不明的系数表可靠
  · 不同厂商同型号的 C 值确实有差异，自建型号库反而会给出错的数
  · 联轴器与减速器的型号表是每型号一行的厂商数据，成千上万条

所以测试的重点不是"选得准不准"，而是：
  1. 算术与手算对得上
  2. 校核项一个都不能少（少一项就等于替用户放行了一个没查的条件）
  3. 表里没有偷偷塞进来的厂商数据
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import Knowledge, load_spec, run  # noqa: E402

KNOW = Knowledge()

# ═══════════════════════ 滚动轴承 ═══════════════════════

BEARING = load_spec("bearing")

# 黄金算例：Fr=3000 N，纯径向，n=1450，C=30000 N，球轴承，fp=1.2
#   P    = 1.2 × (1 × 3000 + 0 × 0) = 3600 N
#   C/P  = 30000 / 3600             = 8.3333
#   L10h = 10⁶/(60×1450) × 8.3333³  = 11.4943 × 578.70 = 6651.4 h
B_GOLDEN = {"Fr": 3000, "Fa": 0, "n": 1450, "C": 30000, "rolling_element": "ball",
            "load_case": "pure_radial", "fp": 1.2, "Lh_required": 20000}


def _bearing(**over):
    return run(BEARING, {**B_GOLDEN, **over}, KNOW)


def test_bearing_life_matches_hand_calculation():
    out = _bearing().outputs
    assert out["P"] == pytest.approx(3600)
    assert out["C_over_P"] == pytest.approx(30000 / 3600)
    assert out["epsilon"] == 3
    assert out["L10h"] == pytest.approx(
        1_000_000 / (60 * 1450) * (30000 / 3600) ** 3)


def test_pure_radial_coefficients_need_no_lookup_table():
    """纯径向时 X=1、Y=0 是定义性的，不是查来的系数。
    这一档因此不依赖那张没录入的 X/Y 表。"""
    out = _bearing().outputs
    assert (out["X"], out["Y"]) == (1, 0)


def test_combined_load_takes_x_y_from_the_catalog():
    """联合载荷必须由用户从样本填 X、Y——引擎没有这张表，也不假装有。"""
    out = _bearing(load_case="combined", Fa=1500, X_in=0.56, Y_in=1.45).outputs
    assert out["X"] == 0.56 and out["Y"] == 1.45
    assert out["P"] == pytest.approx(1.2 * (0.56 * 3000 + 1.45 * 1500))


def test_roller_bearings_use_the_ten_thirds_exponent():
    """ε 由接触形式决定：球点接触 3，滚子线接触 10/3。这是 ISO 281 的定义值。"""
    assert _bearing(rolling_element="ball").outputs["epsilon"] == 3
    assert _bearing(rolling_element="roller").outputs["epsilon"] == pytest.approx(
        10 / 3, rel=1e-9)


def test_life_is_reported_short_when_it_is_short():
    """寿命不够就是不够。这条守的是"把校核悄悄放宽"这类改动。"""
    short = _bearing(Fr=9000)
    assert short.status == "check_failed"
    assert short.outputs["L10h"] < short.outputs["Lh_required"]


def test_bearing_table_holds_no_vendor_catalogue_data():
    """表里只能有 ISO 281 的定义值，不能出现任何型号或额定载荷。
    后来人"顺手"塞一张型号表进来，这条会红。"""
    flat = str(KNOW.table("bearing", "life_calc").data)
    for token in ("6205", "6206", "C0:", "型号"):
        assert token not in flat, token


# ═══════════════════════ 联轴器 ═══════════════════════

COUPLING = load_spec("coupling")

# 黄金算例：7.5 kW / 1450 r/min，电动机驱动（Kw=1），K工作机=1.5
#   T  = 9550 × 7.5 / 1450 = 49.3966 N·m
#   Tc = 1 × 1.5 × 49.3966 = 74.0948 N·m
C_GOLDEN = {"P": 7.5, "n": 1450, "prime_mover": "class_1", "K_work": 1.5,
            "Tn_catalog": 250, "n_allow_catalog": 4000, "d_shaft": 42,
            "bore_min_catalog": 30, "bore_max_catalog": 55}


def _coupling(**over):
    return run(COUPLING, {**C_GOLDEN, **over}, KNOW)


def test_coupling_torque_matches_hand_calculation():
    out = _coupling().outputs
    assert out["T"] == pytest.approx(9550 * 7.5 / 1450)
    assert out["Kw"] == 1.0
    assert out["Tc"] == pytest.approx(1.0 * 1.5 * 9550 * 7.5 / 1450)
    assert out["margin"] == pytest.approx(250 / out["Tc"])


def test_fewer_cylinders_means_a_bigger_prime_mover_factor():
    """Kw 反映驱动端的转矩波动：缸数越少波动越大。顺序错了说明表抄反了。"""
    factors = {c: _coupling(prime_mover=c).outputs["Kw"]
               for c in ("class_1", "class_2", "class_3", "class_4")}
    assert factors["class_1"] < factors["class_2"] < factors["class_3"] < factors["class_4"]


def test_all_four_catalogue_conditions_are_checked():
    """转矩、转速、孔径上下限——四项都要校核。
    少一项就等于替用户放行了一个他没查的条件。"""
    names = {c["name_zh"] for c in _coupling().checks}
    assert names == {"公称转矩校核", "许用转速校核", "轴径下限校核", "轴径上限校核"}


@pytest.mark.parametrize("override,failing", [
    ({"Tn_catalog": 63}, "公称转矩校核"),
    ({"n": 5000}, "许用转速校核"),
    ({"d_shaft": 80}, "轴径上限校核"),
    ({"d_shaft": 20}, "轴径下限校核"),
])
def test_each_condition_can_fail_on_its_own(override, failing):
    trace = _coupling(**override)
    failed = {c["name_zh"] for c in trace.checks if not c["detail"]["passed"]}
    assert failing in failed
    assert trace.status == "check_failed"


# ═══════════════════════ 减速器 ═══════════════════════

REDUCER = load_spec("reducer")

# 黄金算例：7.5 kW / 1450 r/min，i=4，单级圆柱齿轮 8 级精度，
#           2 对球轴承，1 个弹性联轴器，KA=1.25
#   η总  = 0.97 × 0.99² × 0.99 = 0.97 × 0.9801 × 0.99 = 0.941229...
#   P出  = 7.5 × η总
#   n出  = 1450 / 4 = 362.5 r/min
#   T出  = 9550 × P出 / n出
R_GOLDEN = {"P": 7.5, "n_in": 1450, "i": 4, "drive_type": "cylindrical_single",
            "gear_precision": "grade_8", "bearing_pairs": 2,
            "bearing_type": "rolling_ball", "coupling_count": 1,
            "coupling_type": "elastic", "KA": 1.25}


def _reducer(**over):
    return run(REDUCER, {**R_GOLDEN, **over}, KNOW)


def test_reducer_efficiency_is_a_product_of_every_stage():
    """总效率是各级连乘。同类型的几对轴承、几个联轴器要**分别**计入——
    漏乘一项，输出功率会偏高，选型就会选小一档。"""
    out = _reducer().outputs
    assert out["eta_total"] == pytest.approx(0.97 * 0.99 ** 2 * 0.99)
    assert out["P_out"] == pytest.approx(7.5 * out["eta_total"])
    assert out["n_out"] == pytest.approx(1450 / 4)
    assert out["T_out"] == pytest.approx(9550 * out["P_out"] / out["n_out"])
    assert out["T_required"] == pytest.approx(1.25 * out["T_out"])


def test_two_stage_squares_the_gear_efficiency():
    """两级是两对齿轮串联，效率要乘两次。"""
    one = _reducer(i=4, drive_type="cylindrical_single").outputs["eta_drive"]
    two = _reducer(i=20, drive_type="cylindrical_two").outputs["eta_drive"]
    assert two == pytest.approx(one ** 2)


def test_more_bearing_pairs_lowers_the_total_efficiency():
    effs = [_reducer(bearing_pairs=k).outputs["eta_total"] for k in (0, 1, 2, 4)]
    assert effs == sorted(effs, reverse=True)


def test_worm_drive_efficiency_uses_the_conservative_end():
    """区间一律取下端。选型时低估输出功率会选大一档，高估会选小一档，前者安全。"""
    table = KNOW.table("reducer", "transmission").data["worm_drive"]["by_thread"]
    for thread in ("self_locking", "single", "double", "quad"):
        got = _reducer(i=25, drive_type="worm", worm_thread=thread).outputs["eta_drive"]
        assert got == pytest.approx(table[thread]["eta_min"])


def test_single_stage_ratio_beyond_the_usual_range_fails_the_check():
    """单级圆柱齿轮常用传动比 3~5。要 12 就该改两级，而不是硬做。"""
    trace = _reducer(i=12)
    failed = {c["name_zh"] for c in trace.checks if not c["detail"]["passed"]}
    assert "传动比合理性校核" in failed


def test_planetary_and_cycloidal_are_absent_rather_than_guessed():
    """教材表 3-1 不含行星与摆线针轮，表里就**没有**它们——
    不能因为"常见"就顺手编两个效率进去。"""
    flat = str(KNOW.table("reducer", "transmission").data)
    assert "planetary" not in flat and "cycloid" not in flat
    kinds = {o["value"] for i in REDUCER.inputs if i.id == "drive_type"
             for o in i.options}
    assert "planetary" not in kinds


def test_bevel_gear_grade_9_reports_a_gap_instead_of_borrowing_a_number():
    """圆锥齿轮表里只有 7 级与 8 级。选 9 级要报缺口，
    不能拿圆柱齿轮的 9 级值顶上——那是两种不同的传动。"""
    trace = _reducer(i=2.5, drive_type="bevel_single", gear_precision="grade_9")
    assert trace.status == "data_missing"
    assert "bevel_closed" in trace.blocker["path"]

"""平键与轴的工作流测试。

两个物料放一个文件，因为它们守的是同一类性质：
公式主导、表小、而**表里的每个数都必须说得出出处**。

轴这边多守一条：缓存里刻意没有存手册的 A0 系数，因为 A0 是 [τT] 的换算结果。
测试验证"不存 A0 直接算"与"用手册 A0 算"给出同一个答案——
这样才敢说省掉那一列不是偷工，是去掉了一处可能抄错的地方。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import Knowledge, load_spec, run  # noqa: E402

KNOW = Knowledge()

# ═══════════════════════ 平键 ═══════════════════════

KEY_SPEC = load_spec("key")

# 黄金算例：φ45 轴，300 N·m，轮毂长 70，A 型键，铸铁轮毂，轻微冲击
#   d=45 落在 (44, 50] → b=14, h=9, t1=5.5, t2=3.8
#   L = 不大于 70 的最大标准键长 = 70
#   l = L − b = 70 − 14 = 56
#   σp = 4 × 300 × 1000 / (45 × 9 × 56) = 1200000 / 22680 = 52.91 MPa
#   铸铁 · 轻微冲击 [σp] = 50 MPa  → 52.91 > 50，校核不通过
KEY_GOLDEN = {"d": 45, "T": 300, "L_hub": 70, "key_type": "A",
              "weak_material": "cast_iron", "load_type": "light_impact"}


def _key(**over):
    return run(KEY_SPEC, {**KEY_GOLDEN, **over}, KNOW)


def test_key_section_comes_from_the_right_interval():
    """标准的区间是左开右闭（>44~50）。边界值归上一行——
    d=44 应当取 12×8 那一行，不是 14×9。"""
    assert _key(d=44).outputs["b"] == 12
    assert _key(d=45).outputs["b"] == 14
    assert _key(d=50).outputs["b"] == 14
    assert _key(d=51).outputs["b"] == 16


def test_key_golden_case_matches_hand_calculation():
    out = _key().outputs
    assert (out["b"], out["h"], out["t1"], out["t2"]) == (14, 9, 5.5, 3.8)
    assert out["L"] == 70
    assert out["l_work"] == 56
    assert out["sigma_p"] == pytest.approx(4 * 300 * 1000 / (45 * 9 * 56))
    assert out["sigma_p_allow"] == 50


def test_cast_iron_hub_is_weaker_and_fails_where_steel_passes():
    """键、轴、轮毂三者取最弱的那个材料。钢轴配铸铁齿轮必须按铸铁查——
    差一倍，选错会让校核轻松通过而轮毂实际被压溃。"""
    iron = _key(weak_material="cast_iron")
    steel = _key(weak_material="steel")
    assert iron.status == "check_failed"
    assert steel.status == "ok"
    assert steel.outputs["sigma_p_allow"] == 100
    # 同样的几何、同样的转矩，只因为材料不同就换了结论
    assert iron.outputs["sigma_p"] == steel.outputs["sigma_p"]


def test_key_working_length_depends_on_key_type():
    """A 型两端圆头去掉 b，B 型方头全长，C 型单圆头去掉 b/2。"""
    assert _key(key_type="A").outputs["l_work"] == 70 - 14
    assert _key(key_type="B").outputs["l_work"] == 70
    assert _key(key_type="C").outputs["l_work"] == 70 - 7


def test_key_length_never_exceeds_the_hub():
    """键长取不大于轮毂长度的最大标准值——比轮毂长的键装不进去。"""
    series = KNOW.table("key", "keyway_dimensions").data["length_series"]["series"]
    for hub in (30, 45, 63, 64, 100, 137):
        L = run(KEY_SPEC, {**KEY_GOLDEN, "L_hub": hub}, KNOW).outputs["L"]
        assert L <= hub
        assert L == max(s for s in series if s <= hub)


def test_shaft_diameter_outside_the_table_is_refused_at_the_input():
    """GB/T 1095 只覆盖轴径 6~500，输入域就按这个范围定。

    所以 φ520 在**进门时**就被挡下并说清是哪一项超限，
    而不是一路算到查表才报缺数据。输入域与数据覆盖范围对齐时，
    这是更好的行为——报错离用户能改的那个输入框更近。"""
    from mds import InputError
    with pytest.raises(InputError) as exc:
        _key(d=520)
    assert exc.value.param == "d"
    assert "500" in str(exc.value)


def test_key_only_checks_bearing_stress_and_says_so():
    """普通平键工程上只校核挤压，不校核剪切——缓存里没有 [τ]。
    这是有意为之，必须在 notes 里交代清楚，不能让人以为剪切也校核过了。"""
    assert all("剪切" not in c["name_zh"] for c in _key().checks)
    assert any("不校核剪切" in n for n in KEY_SPEC.notes)


# ═══════════════════════ 轴 ═══════════════════════

SHAFT_SPEC = load_spec("shaft")

# 黄金算例：45 钢，7.5 kW，1450 r/min，弯矩较大，单键槽
#   T   = 9550 × 7.5 / 1450                       = 49.397 N·m
#   [τT]= 30 MPa（45 钢保守端）
#   d   = (9.55e6 × 7.5 / (0.2 × 30 × 1450))^(1/3) = 20.192 mm
#   ×1.05（单键槽）                                = 21.202 → 向上取整 22 mm
#   τ实 = 9.55e6 × 7.5 / (0.2 × 22³ × 1450)        = 23.195 MPa ≤ 30 ✓
SHAFT_GOLDEN = {"P": 7.5, "n": 1450, "material": "steel_45",
                "bending_influence": "large", "keyway": "one"}


def _shaft(**over):
    return run(SHAFT_SPEC, {**SHAFT_GOLDEN, **over}, KNOW)


def test_shaft_golden_case_matches_hand_calculation():
    out = _shaft().outputs
    assert out["T"] == pytest.approx(9550 * 7.5 / 1450)
    assert out["tau_allow"] == 30
    assert out["d_torsion"] == pytest.approx(
        (9_550_000 * 7.5 / (0.2 * 30 * 1450)) ** (1 / 3))
    assert out["d_shaft"] == 22
    assert out["tau_actual"] == pytest.approx(
        9_550_000 * 7.5 / (0.2 * 22 ** 3 * 1450))


def test_dropping_the_A0_column_gives_the_same_answer():
    """缓存里刻意没存手册的 A0 系数——它是 [τT] 的换算结果，不是独立数据。

    这条验证省掉那一列不是偷工：直接由 [τT] 算出的轴径，
    与用 A0 = (9.55e6/(0.2[τT]))^(1/3) 再乘 (P/n)^(1/3) 算出的完全一致。
    顺带确认算出的 A0 落在手册公布的区间里（45 钢为 118~107）。"""
    out = _shaft().outputs
    tau = out["tau_allow"]
    a0 = (9_550_000 / (0.2 * tau)) ** (1 / 3)
    assert out["d_torsion"] == pytest.approx(a0 * (7.5 / 1450) ** (1 / 3))
    assert 107 <= a0 <= 118, f"45 钢算出的 A0 = {a0:.1f}，不在手册区间内"


def test_weaker_material_needs_a_thicker_shaft():
    """许用应力低 → 轴粗。这条顺序错了说明表抄反了。"""
    diameters = {m: _shaft(material=m).outputs["d_shaft"]
                 for m in ("q235_20", "steel_35", "steel_45", "alloy_40cr")}
    assert diameters["q235_20"] > diameters["steel_35"] >= diameters["steel_45"]
    assert diameters["steel_45"] >= diameters["alloy_40cr"]


def test_bending_influence_picks_the_right_end_of_the_range():
    """弯矩大取区间下端（轴粗），弯矩小取上端（轴细）。"""
    big = _shaft(bending_influence="large")
    small = _shaft(bending_influence="small")
    assert big.outputs["tau_allow"] < small.outputs["tau_allow"]
    assert big.outputs["d_shaft"] > small.outputs["d_shaft"]


def test_keyway_enlarges_the_diameter():
    base = _shaft(keyway="none").outputs["d_raw"]
    one = _shaft(keyway="one").outputs["d_raw"]
    two = _shaft(keyway="two").outputs["d_raw"]
    assert one == pytest.approx(base * 1.05)
    assert two == pytest.approx(base * 1.10)


def test_rounding_up_never_breaks_the_strength_check():
    """向上取整只会让轴更粗，所以反算的实际应力必须始终通过校核。
    这条守的是取整方向写反（用 floor）这类低级错误。"""
    for P in (0.5, 1.5, 7.5, 22, 55, 200):
        for material in ("q235_20", "steel_45", "alloy_40cr"):
            trace = _shaft(P=P, material=material)
            assert trace.status == "ok", (P, material)
            assert trace.outputs["tau_actual"] <= trace.outputs["tau_allow"]


def test_shaft_declares_it_is_only_an_estimate():
    """只按纯扭转初估，不做弯扭合成与疲劳校核。
    这个边界必须写在 notes 里——否则有人会拿它直接定稿。"""
    assert any("初估" in n and "疲劳" in n for n in SHAFT_SPEC.notes)

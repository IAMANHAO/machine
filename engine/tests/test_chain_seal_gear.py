"""链传动 / O 形密封圈 / 齿轮 —— 三个"图表主导"物料的工作流测试。

这三个是计划里就预告会留明显缺口的：齿轮的 ZH、YFa、σHlim 是线图，
链传动的额定功率是曲线，密封圈的沟槽尺寸拿不到。

所以测试的重点是**边界守得住**：
  1. 能算的部分（几何、运动学）要算对
  2. 不能算的部分要明确不做，而不是偷偷拿一个近似值顶上
  3. 表里绝不能出现"看起来像那么回事"的编造数值
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mds import Knowledge, load_spec, run  # noqa: E402

KNOW = Knowledge()

# ═══════════════════════ 滚子链 ═══════════════════════

CHAIN = load_spec("chain")

# 黄金算例：08B，z1=21，z2=63，n1=300 r/min，a0=600 mm
#   p  = 12.7 mm
#   v  = 21 × 12.7 × 300 / 60000 = 1.3335 m/s
#   d1 = 12.7 / sin(π/21)        = 85.2107 mm
CH_GOLDEN = {"chain_no": "c08B", "z1": 21, "z2": 63, "n1": 300, "a0": 600}


def _chain(**over):
    return run(CHAIN, {**CH_GOLDEN, **over}, KNOW)


def test_chain_pitch_can_be_recomputed_from_the_chain_number():
    """链号的前两位是节距的 1/16 英寸数。这是整张表敢录的唯一理由——
    其余参数（滚子直径、排距、极限拉伸载荷）的转载列与表头错位，一律没录。"""
    chains = KNOW.table("chain", "chain_pitch").data["chains"]
    for key, row in chains.items():
        code = row["code"]
        if code == "05B":          # 公制例外，标准取 8.0 而非 7.9375
            assert row["pitch"] == 8.0
            continue
        want = int(code[:2]) / 16 * 25.4
        assert row["pitch"] == pytest.approx(want, abs=5e-4), code


def test_chain_table_holds_nothing_it_could_not_verify():
    """那张转载表把销轴直径当成了内宽、把链板高度当成了极限拉伸载荷。
    宁可不录，也不能让一个错位的数字流进计算。"""
    flat = str(KNOW.table("chain", "chain_pitch").data)
    for token in ("tensile", "极限拉伸", "roller_d", "b1", "pt"):
        assert token not in flat, token


def test_chain_kinematics_match_hand_calculation():
    out = _chain().outputs
    assert out["p"] == 12.7
    assert out["v"] == pytest.approx(21 * 12.7 * 300 / 60000)
    assert out["d1"] == pytest.approx(12.7 / math.sin(math.pi / 21))
    assert out["d2"] == pytest.approx(12.7 / math.sin(math.pi / 63))
    assert out["i"] == 3


def test_chain_link_count_is_always_even():
    """奇数链节需要过渡链节，强度下降约 20%。圆整必须落在偶数上。"""
    for z1, z2, a0 in [(21, 63, 600), (17, 85, 450), (19, 38, 800), (23, 46, 1200)]:
        out = _chain(z1=z1, z2=z2, a0=a0).outputs
        assert out["Lp"] % 2 == 0, (z1, z2, a0)


def test_actual_centre_distance_stays_near_the_initial_guess():
    """链节取整后中心距会微调，但不该偏离初值太多——
    偏太多说明链长公式抄错了。"""
    for a0 in (400, 600, 900, 1500):
        out = _chain(a0=a0).outputs
        assert abs(out["a"] - a0) < 0.5 * out["p"], a0


def test_chain_cannot_select_a_chain_number():
    """选链号要查额定功率曲线，缓存里没有。链号必须是输入——
    这条守的是后来人加一个"自动选链号"的步骤却没有依据。"""
    assert any(i.id == "chain_no" and i.required for i in CHAIN.inputs)
    assert any("不能替你选链号" in n for n in CHAIN.notes)


# ═══════════════════════ O 形密封圈 ═══════════════════════

SEAL = load_spec("seal")

# 黄金算例：d2=2.65，沟槽 2.1×3.6，NBR，60℃
#   压缩率  = (2.65 − 2.1) / 2.65 × 100 = 20.755 %
#   填充率  = π×2.65²/4 / (2.1×3.6) × 100 = 5.5155/7.56 ×100 = 72.96 %
SE_GOLDEN = {"d2_in": 2.65, "d1": 40, "groove_depth": 2.1,
             "groove_width": 3.6, "material": "nbr", "T_work": 60}


def _seal(**over):
    return run(SEAL, {**SE_GOLDEN, **over}, KNOW)


def test_seal_golden_case_matches_hand_calculation():
    out = _seal().outputs
    assert out["d2"] == 2.65
    assert out["compression"] == pytest.approx((2.65 - 2.1) / 2.65 * 100)
    assert out["fill_ratio"] == pytest.approx(
        math.pi * 2.65 ** 2 / 4 / (2.1 * 3.6) * 100)


def test_section_diameter_snaps_to_the_standard_series_visibly():
    """GB/T 3452.1 的 d2 只有 5 档。用圆整而不是下拉框，
    是为了让「你填的 2.7 被取成 2.65」出现在计算过程里，而不是悄悄发生在界面上。"""
    out = _seal(d2_in=2.7).outputs
    assert out["d2"] == 2.65
    step = next(s for s in _seal(d2_in=2.7).steps if s["id"] == "d2")
    assert "2.7" in step["substitution"] and "2.65" in step["substitution"]


def test_compression_and_fill_are_reported_but_not_judged():
    """合格判据在 GB/T 3452.3 里，缓存中没有。
    引擎只报数不判定——凭印象设一个阈值等于编了一条判据。"""
    checks = {c["name_zh"] for c in _seal().checks}
    assert "压缩率有效性校核" in checks          # 只守"确实被压缩了"这条结构性底线
    assert not any("填充率" in c for c in checks)
    table = KNOW.table("seal", "oring").data
    assert table["compression_limits"] == {}
    assert table["fill_ratio_limits"] == {}


@pytest.mark.parametrize("material,T,should_fail", [
    ("nbr", 60, False),
    ("nbr", 150, True),        # 超出 NBR 上限 100℃
    ("fkm", 180, False),
    ("fkm", -40, True),        # 低于 FKM 下限 −20℃
    ("vmq", 150, False),
    ("epdm", -30, False),
])
def test_material_temperature_window_is_enforced(material, T, should_fail):
    trace = _seal(material=material, T_work=T)
    failed = [c for c in trace.checks if not c["detail"]["passed"]]
    assert bool(failed) is should_fail, (material, T)


def test_seal_temperature_ranges_are_the_conservative_intersection():
    """两处信源差异很大（NBR 甲 −55~150、乙 −25~100）。本表取保守交集。
    取宽的那个会让人以为 NBR 能用到 150℃。"""
    mats = KNOW.table("seal", "oring").data["materials"]
    assert mats["nbr"]["temp_max"] == 100
    assert mats["fkm"]["temp_min"] == -20
    assert mats["vmq"]["temp_max"] == 200


# ═══════════════════════ 齿轮 ═══════════════════════

GEAR = load_spec("gear")

# 黄金算例：mn=2，z1=20，z2=60，直齿，不变位
#   d1 = 40，d2 = 120，a = 80
#   da1 = 40 + 2×2×1 = 44      df1 = 40 − 2×2×1.25 = 35
GE_GOLDEN = {"m_n": 2, "z1": 20, "z2": 60, "beta": 0, "x1": 0, "x2": 0,
             "psi_d": 1.0, "T1": 100, "material_pair": "steel_steel",
             "ZH": 2.5, "K": 1.5, "sigma_HP": 600}


def _gear(**over):
    return run(GEAR, {**GE_GOLDEN, **over}, KNOW)


def test_gear_geometry_matches_hand_calculation():
    out = _gear().outputs
    assert out["d1"] == pytest.approx(40)
    assert out["d2"] == pytest.approx(120)
    assert out["a"] == pytest.approx(80)
    assert out["da1"] == pytest.approx(44)
    assert out["df1"] == pytest.approx(35)
    assert out["b"] == pytest.approx(40)


def test_helical_gear_uses_the_transverse_module():
    """斜齿的分度圆按端面模数算：mt = mn / cos β。
    忘了除 cos β 会把中心距算小，装配时对不上。"""
    for beta in (0, 8, 15, 20):
        out = _gear(beta=beta).outputs
        assert out["d1"] == pytest.approx(2 * 20 / math.cos(math.radians(beta)))
        assert out["a"] == pytest.approx(
            2 * (20 + 60) / (2 * math.cos(math.radians(beta))))


def test_profile_shift_moves_the_tip_and_root_circles():
    """变位系数直接加到齿顶高上：da = d + 2mn(ha* + x)。"""
    plus = _gear(x1=0.3).outputs
    assert plus["da1"] == pytest.approx(40 + 2 * 2 * (1 + 0.3))
    assert plus["df1"] == pytest.approx(40 - 2 * 2 * (1 + 0.25 - 0.3))


def test_contact_ratio_is_skipped_for_profile_shifted_gears():
    """变位齿轮的重合度要先解啮合角 α'，那是个超越方程，v1 没做。
    不做就要明确跳过，不能拿标准齿轮的公式硬算一个错的数。"""
    shifted = _gear(x1=0.3, x2=-0.3)
    skipped = [s["name_zh"] for s in shifted.steps if s["status"] == "not_applicable"]
    assert "端面重合度" in skipped
    assert "eps_alpha" not in shifted.outputs
    # 标准齿轮则必须算出来
    assert _gear().outputs["eps_alpha"] > 1


def test_contact_ratio_of_a_standard_spur_pair_is_plausible():
    """20/60 标准直齿的端面重合度应在 1.6~1.8 之间。
    这条守的是 acos / tan 写错位置这类错误——结果会立刻离谱。"""
    assert 1.6 < _gear().outputs["eps_alpha"] < 1.8


def test_gear_does_not_pretend_to_do_a_full_strength_calculation():
    """ZH、YFa、YSa、σHlim 都是线图，读不出数。
    表里必须是空的，边界必须写在 notes 里——这是最容易被后来人"补全"的地方。"""
    table = KNOW.table("gear", "gear_basic").data
    assert table["node_region_factor_ZH"] == {}
    assert table["tooth_form_factor_YFa"] == {}
    assert table["fatigue_limits"] == {}
    assert any("不是完整的 GB/T 3480" in n for n in GEAR.notes)
    # 弯曲强度校核完全不做
    assert not any("弯曲" in c["name_zh"] for c in _gear().checks)


def test_contact_stress_scales_with_the_square_root_of_torque():
    """σH ∝ √T。翻四倍转矩，应力翻一倍。"""
    base = _gear(T1=100).outputs["sigma_H"]
    quad = _gear(T1=400).outputs["sigma_H"]
    assert quad == pytest.approx(2 * base)


def test_overloaded_gear_fails_the_contact_check():
    trace = _gear(T1=2000)
    assert trace.status == "check_failed"
    failed = [c["name_zh"] for c in trace.checks if not c["detail"]["passed"]]
    assert "接触强度校核" in failed

"""参数补齐、叫法对齐、改法建议 —— 让流程别卡住，但每个值都留下出身。

三条接口都来自同一个要求：**不要把用户堵死在一个报错上**。
但"不堵死"不等于"随便放个数进去"，所以这里守的是那条分界线：

- 补进来的值走**与手输完全相同**的校验闸门
- 说不出理由的值**不配**直接写进参数表
- 对齐**只能在给定候选里选**，造不出新选项
- 每一个 AI 给的值都在结果警告里留下出身

用同步带（随包物料，有真实的参数域与枚举）来测，不用捏造的规格。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def bound(env, bind_as, fake):
    client, _vault = env
    bind_as("deepseek")
    fake.script = {}
    fake.calls = []
    return client, fake


# ── 参数补齐 ───────────────────────────────────────────────────────

def test_filled_values_go_through_the_same_gate_as_typing_them_in(bound):
    client, fake = bound
    fake.script["fill"] = {"filled": {
        "P": {"value": 5.5, "rationale": "按常见三相异步电机的额定功率档取"},
        # 超出参数域（n1 上限 20000）—— 与手输同一条通道会拦下
        "n1": {"value": 999999, "rationale": "随便给个大的"},
    }, "questions": []}

    r = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {}})
    assert r.status_code == 200
    body = r.json()

    assert body["filled"]["P"]["value"] == 5.5
    assert "n1" not in body["filled"]
    assert "没通过参数校验" in body["rejected"]["n1"]


def test_a_value_without_a_reason_is_not_written_into_the_form(bound):
    """说不出为什么的数不配直接写进参数表 —— 它就是"来路不明"的定义。"""
    client, fake = bound
    fake.script["fill"] = {"filled": {"P": {"value": 5.5, "rationale": ""}},
                           "questions": []}

    body = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {}}).json()
    assert "P" not in body["filled"]
    assert "没有给出理由" in body["rejected"]["P"]


def test_it_asks_instead_of_guessing_what_would_change_the_answer(bound):
    client, fake = bound
    fake.script["fill"] = {"filled": {}, "questions": [
        {"id": "work_machine", "ask": "带动的是什么设备？", "why": "工况系数全看它"},
        {"id": "x", "ask": "第二问", "why": "…"},
        {"id": "y", "ask": "第三问", "why": "…"},
        {"id": "z", "ask": "第四问 —— 超出 3 条会被截掉", "why": "…"},
    ]}

    body = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {}}).json()
    assert len(body["questions"]) == 3          # 一次最多问 3 条
    assert body["questions"][0]["ask"] == "带动的是什么设备？"
    assert body["still_missing"]


def test_the_users_reply_is_carried_into_the_next_round(bound):
    client, fake = bound
    fake.script["fill"] = {"filled": {}, "questions": []}
    client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {},
        "reply": "带动的是螺杆输送机，每天两班"})

    sent = fake.calls[-1]
    assert sent["turns"] == 2                   # system + user，没有多余轮次
    # 回答要真的进到提问里去 —— 不然"对话式"就是假的
    assert fake.last_user and "螺杆输送机" in fake.last_user


def test_optional_params_are_left_to_the_spec_not_to_the_ai(bound):
    """只补**必填**项。

    可选项在工作流规格里有回退式（`default` 步骤），那是**有出处的**值；
    让 AI 猜一个填进去，等于把一个可追溯的数换成一个不可追溯的数——方向反了。
    真正会把用户堵住的是缺必填项，补那个就够。
    """
    client, fake = bound
    fake.script["fill"] = {"filled": {
        "a0": {"value": 400, "rationale": "看着挺合理"},   # a0 是可选项
    }, "questions": []}

    body = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {}}).json()
    assert "a0" not in body["filled"]
    assert "不在待补清单里" in body["rejected"]["a0"]


def test_a_param_the_user_already_filled_is_never_touched(bound):
    client, fake = bound
    fake.script["fill"] = {"filled": {
        "n1": {"value": 999, "rationale": "我觉得该是这个"}}, "questions": []}

    body = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {"n1": 1450}}).json()
    assert "n1" not in body["filled"]
    assert "已经填了" in body["rejected"]["n1"]


def test_fill_is_closed_offline_with_an_honest_reason(client):
    r = client.post("/api/ai/fill-params", json={
        "material": "synchronous_belt", "known": {}})
    assert r.status_code == 409
    assert "离线" in r.json()["detail"]["message"]


# ── 叫法对齐 ───────────────────────────────────────────────────────

CANDS = [{"value": "烧结钕铁硼", "label": "烧结钕铁硼（高磁能积）"},
         {"value": "铁氧体", "label": "铁氧体（低成本）"}]


def test_alignment_maps_a_synonym_onto_a_real_candidate(bound):
    client, fake = bound
    fake.script["align"] = {"value": "烧结钕铁硼", "confidence": "high",
                            "why": "N35 是烧结钕铁硼的常见牌号"}

    body = client.post("/api/ai/align", json={
        "label": "磁铁材料牌号", "value": "N35", "candidates": CANDS}).json()
    assert body["value"] == "烧结钕铁硼"
    assert body["confidence"] == "high"


def test_alignment_cannot_invent_a_candidate(bound):
    """**结构性保证**：它只能在给定集合里指一个，造不出新选项。"""
    client, fake = bound
    fake.script["align"] = {"value": "钐钴", "confidence": "high", "why": "我编的"}

    body = client.post("/api/ai/align", json={
        "label": "磁铁材料牌号", "value": "N35", "candidates": CANDS}).json()
    assert body["value"] is None
    assert "不在候选里" in body["why"]


def test_something_with_no_referent_stays_unaligned(bound):
    """"其它"不是叫法不同，是他还没定。对不上就该对不上。"""
    client, fake = bound
    fake.script["align"] = {"value": None, "confidence": "low",
                            "why": "「其它」没有具体所指"}

    body = client.post("/api/ai/align", json={
        "label": "磁铁材料牌号", "value": "other", "candidates": CANDS}).json()
    assert body["value"] is None


def test_a_broken_alignment_reply_is_not_fatal(bound):
    """对不上是常态，不该抛异常把整个流程炸掉。"""
    client, fake = bound
    fake.script["align"] = "这不是 JSON"

    r = client.post("/api/ai/align", json={
        "label": "磁铁材料牌号", "value": "N35", "candidates": CANDS})
    assert r.status_code == 200
    assert r.json()["value"] is None


# ── 改法建议 ───────────────────────────────────────────────────────

def test_a_suggested_fix_must_itself_pass_validation(bound):
    """建议值是建议，不是特权 —— 照样过闸门。"""
    client, fake = bound
    fake.script["fix"] = {"explain": "中心距太小，带长会不够绕",
                          "suggestion": 99999, "how": "我随便给的", "ask": ""}

    body = client.post("/api/ai/advise-fix", json={
        "material": "synchronous_belt", "param": "a0", "value": 1,
        "problem": "小于允许下限", "known": {"P": 5.5}}).json()
    assert body["suggestion"] is None
    assert "自己也没过校验" in body["rejected"]
    # 但解释要留下来 —— 那是用户真正需要的东西
    assert "带长" in body["explain"]


def test_a_good_fix_comes_back_with_how_it_was_derived(bound):
    client, fake = bound
    fake.script["fix"] = {"explain": "中心距小于两轮半径之和",
                          "suggestion": 400, "how": "按 0.7(d1+d2) 的下限估的",
                          "ask": ""}

    body = client.post("/api/ai/advise-fix", json={
        "material": "synchronous_belt", "param": "a0", "value": 1,
        "problem": "小于允许下限", "known": {"P": 5.5}}).json()
    assert body["suggestion"] == 400
    assert body["how"]
    assert body["name_zh"]


# ── 出身：AI 给的值必须在结果里留痕 ────────────────────────────────

GOLDEN = {"P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
          "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
          "hours_per_day": "h_le_10"}


def test_ai_filled_values_are_named_in_the_result_warning(client):
    """一个标着"AI 按常用值补的"的数不是来路不明的数；
    一个混在手输里、看不出区别的数才是。"""
    r = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": GOLDEN, "save": False,
        "ai_filled": {"a0": "取推荐区间中部"},
        "ai_aligned": {"belt_type": "H"}})
    assert r.status_code == 200

    warnings = " ".join(r.json()["trace"]["warnings"])
    assert "a0" in warnings and "取推荐区间中部" in warnings
    assert "校验只管取值合法，不管它适不适合你的工况" in warnings
    assert "belt_type → H" in warnings


def test_a_run_without_ai_help_says_nothing_about_ai(client):
    """没让 AI 插手就不该凭空多一条警告。"""
    r = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": GOLDEN, "save": False})
    warnings = " ".join(r.json()["trace"]["warnings"])
    assert "AI" not in warnings

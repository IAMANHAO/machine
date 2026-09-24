"""引导式选型 —— 按 SKILL.md 的阶段走，AI 引导，用户做选择。

这个文件守的是**替代旧版「AI 一次性起草」的那些理由**：

1. 依据要真的取证，对不上的选不了
2. 公式里不准出现编出来的系数（旧闸门放过了 `1.25 * F`）
3. 每个公式的 source.ref 要落在用户确认的依据之内
4. 不合规不直接甩给用户 —— 先退回给模型让它改，改不好才轮到用户
5. 闸门在后端，不在前端按钮的置灰状态上
"""

from __future__ import annotations

import pytest

from server import guided, research, search_providers

MECHTOOL = "https://www.mechtool.cn/key.html"
HANDBOOK = "https://www.jlc-jdgf.com/mcbook/key"
CLAIM = "GB/T 1095-2003《普通型 平键》表 1"

# ── 剧本：一份"好的"三阶段输出 ─────────────────────────────────────

GOOD_BASIS = {
    "candidates": [
        {"id": "b1", "claim": CLAIM, "standard": "GB/T 1095-2003",
         "urls": [MECHTOOL], "why": "平键剖面尺寸的直接依据",
         "outline": ["按轴径查键的剖面尺寸", "算挤压应力", "校核挤压应力"]},
    ],
    "material_id": "magnet_plate", "name_zh": "磁吸铁片", "notes": "",
}

GOOD_INPUTS = {
    "inputs": [
        {"id": "T", "name_zh": "传递扭矩", "unit": "N·m", "type": "number",
         "required": True, "round": 1, "domain": {"min": 0.1, "max": 100000},
         "from_handbook": False, "hint": "由电机功率与转速算得"},
        {"id": "d", "name_zh": "轴径", "unit": "mm", "type": "number",
         "required": True, "round": 1, "domain": {"min": 6, "max": 500},
         "from_handbook": False, "hint": "按轴的设计结果给定"},
        {"id": "h", "name_zh": "键高", "unit": "mm", "type": "number",
         "required": True, "round": 1, "domain": {"min": 2, "max": 60},
         "from_handbook": True, "hint": "查 GB/T 1095-2003 表 1 键的剖面尺寸"},
        {"id": "l", "name_zh": "键的工作长度", "unit": "mm", "type": "number",
         "required": True, "round": 1, "domain": {"min": 5, "max": 500},
         "from_handbook": False, "hint": "键长减去键宽"},
        {"id": "sigma_p", "name_zh": "许用挤压应力", "unit": "MPa",
         "type": "number", "required": True, "round": 2,
         "domain": {"min": 1, "max": 600}, "from_handbook": True,
         "hint": "查《机械设计手册》键连接一节的许用挤压应力表，按材料与载荷性质取"},
    ],
    "notes": "许用挤压应力必须你自己查表确认",
}

GOOD_STEPS = {
    "steps": [
        {"id": "sigma", "kind": "formula", "name_zh": "挤压应力",
         "expr": "4 * T * 1000 / (d * h * l)", "unit": "MPa",
         "source": {"ref": "GB/T 1095-2003 表 1 附注"}, "outputs": ["sigma"]},
        {"id": "chk", "kind": "check", "name_zh": "挤压应力校核",
         "value": "sigma", "op": "<=", "limit": "sigma_p", "unit": "MPa",
         "on_fail": "加长键长或改用双键",
         "source": {"ref": "GB/T 1095-2003 强度校核"}},
    ],
    "result": [{"label": "挤压应力", "value": "{sigma}", "unit": "MPa"}],
    "procure": {"material_template": "generic", "channels": ["taobao"],
                "fields": {"kind": "磁吸铁片"}},
    "missing_inputs": [], "notes": ["只做挤压校核，未做剪切校核"],
    "confidence_note": "许用挤压应力取值",
}


def _doc(url: str, text: str, *, ok: bool = True) -> research.Doc:
    return research.Doc(url=url, final_url=url, title="平键",
                        text=text if ok else "", fetched_at="2026-09-24T00:00:00+00:00",
                        fingerprint="abc123", status=200 if ok else 404,
                        error="" if ok else "HTTP 404")


# 当前测试正在用的那个 FakeProvider 类，由 guided_env 夹具填进来。
# 不能在模块顶部 import —— tests/ 不是包，那样会拿到类的第二个副本
# （见 conftest.py 的 fake 夹具）。
_FAKE = None


@pytest.fixture()
def guided_env(env, bind_as, monkeypatch, fake):
    """绑好账号 + 假检索 + 假抓取。**引擎是真的**，只有网络是假的。"""
    global _FAKE
    _FAKE = fake
    client, _vault = env
    bind_as("deepseek")

    pages = {
        MECHTOOL: "GB/T 1095-2003 普通型 平键 剖面尺寸 b h 键槽深度 t1 t2",
        HANDBOOK: "成大先《机械设计手册》键连接 GB/T 1095-2003 许用挤压应力",
    }

    def fake_search(query, *, root, client=None, provider_search=None, limit=8):
        return search_providers.SearchOutcome(
            hits=[search_providers.Hit(title="平键 GB/T 1095", url=MECHTOOL,
                                       snippet="剖面尺寸", origin="site:mechtool.cn"),
                  search_providers.Hit(title="机械设计手册 键连接", url=HANDBOOK,
                                       snippet="许用挤压应力", origin="site:jlc-jdgf.com")],
            rung="whitelist", detail="测试用假检索")

    monkeypatch.setattr(search_providers, "search", fake_search)
    monkeypatch.setattr(research, "fetch",
                        lambda url, **kw: _doc(url, pages.get(url, "")
                                               if url in pages else "", ok=url in pages))
    fake.script = {}
    fake.calls = []
    return client


def _script(**kw):
    _FAKE.script.update(kw)


def _walk_to(client, stage: str) -> str:
    """把会话推进到某个阶段，返回 sid。剧本要先摆好。"""
    sid = client.post("/api/guided", json={"material_text": "磁吸铁片"}).json()["id"]
    if stage == "new":
        return sid
    assert client.post(f"/api/guided/{sid}/research").status_code == 200
    if stage == "basis":
        return sid
    assert client.post(f"/api/guided/{sid}/basis",
                       json={"basis_id": "b1"}).status_code == 200
    if stage == "basis_chosen":
        return sid
    assert client.post(f"/api/guided/{sid}/inputs").status_code == 200
    assert client.post(f"/api/guided/{sid}/inputs/confirm",
                       json={}).status_code == 200
    if stage == "inputs_confirmed":
        return sid
    assert client.post(f"/api/guided/{sid}/steps").status_code == 200
    if stage == "steps":
        return sid
    assert client.post(f"/api/guided/{sid}/steps/confirm",
                       json={"confirmed": True}).status_code == 200
    return sid


# ── 阶段 1：依据必须真的取证 ───────────────────────────────────────

def test_research_corroborates_every_candidate_server_side(guided_env):
    _script(basis=GOOD_BASIS)
    sid = _walk_to(guided_env, "basis")
    body = guided_env.get(f"/api/guided/{sid}").json()

    assert body["stage"] == "basis"
    ev = body["candidates"][0]["evidence"]
    # mechtool.cn 是用户指定的可信站点，单独一处也够往下走
    assert ev["status"] == "trusted"
    assert ev["usable"]
    assert body["usable_candidates"] == 1


def test_url_not_in_search_results_is_sent_back_to_the_model(guided_env):
    """AI 凭记忆编的 URL 不算数 —— 但先退回去让它改，不是直接报错给用户。"""
    made_up = dict(GOOD_BASIS)
    made_up = {**GOOD_BASIS, "candidates": [
        {**GOOD_BASIS["candidates"][0], "urls": ["https://made-up.example/gb1095"]}]}
    _script(basis=[made_up, GOOD_BASIS])

    sid = _walk_to(guided_env, "basis")
    log = guided_env.get(f"/api/guided/{sid}").json()["repair_log"]["basis"]
    assert len(log) == 2
    assert not log[0]["passed"] and log[1]["passed"]
    assert any("检索结果里没有的 URL" in r for r in log[0]["reasons"])


def test_a_basis_whose_body_lacks_the_standard_cannot_be_chosen(guided_env):
    """抓到了正文却没有它声称的标准号 —— 这是最该拦的情况。"""
    _script(basis={**GOOD_BASIS, "candidates": [
        {**GOOD_BASIS["candidates"][0], "claim": "GB/T 9999-2099《根本不存在》"}]})
    sid = _walk_to(guided_env, "basis")

    body = guided_env.get(f"/api/guided/{sid}").json()
    assert body["candidates"][0]["evidence"]["status"] == "unverified"
    assert body["usable_candidates"] == 0

    r = guided_env.post(f"/api/guided/{sid}/basis", json={"basis_id": "b1"})
    assert r.status_code == 422
    assert r.json()["detail"]["kind"] == "unverified_basis"


def test_user_can_supply_their_own_basis_and_it_is_labelled_as_such(guided_env):
    _script(basis=GOOD_BASIS)
    sid = _walk_to(guided_env, "basis")
    r = guided_env.post(f"/api/guided/{sid}/basis", json={
        "custom": {"claim": "我手上的纸质《机械设计手册》第3卷 第10篇",
                   "outline": ["查表", "校核"]}})
    assert r.status_code == 200
    basis = r.json()["basis"]
    # 引擎不去核用户自己的话，但要如实记下这是用户填的
    assert basis["status"] == "self_declared"
    assert basis["confirmed_by"] == "user"


def test_no_search_results_is_reported_not_papered_over(guided_env, monkeypatch):
    monkeypatch.setattr(search_providers, "search",
                        lambda q, **kw: search_providers.SearchOutcome(rung="none"))
    sid = _walk_to(guided_env, "new")
    r = guided_env.post(f"/api/guided/{sid}/research")
    assert r.status_code == 422
    assert r.json()["detail"]["kind"] == "no_search_results"


# ── 顺序闸门：在后端，不在前端按钮上 ───────────────────────────────

def test_cannot_enter_stage2_without_a_confirmed_basis(guided_env):
    _script(basis=GOOD_BASIS)
    sid = _walk_to(guided_env, "basis")
    r = guided_env.post(f"/api/guided/{sid}/inputs")
    assert r.status_code == 409
    assert r.json()["detail"]["stage"] == "basis"


def test_cannot_propose_steps_before_inputs_are_confirmed(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS)
    sid = _walk_to(guided_env, "basis_chosen")
    assert guided_env.post(f"/api/guided/{sid}/inputs").status_code == 200
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 409


def test_cannot_run_without_confirming_the_formulas(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "steps")
    r = guided_env.get(f"/api/guided/{sid}/spec")
    assert r.status_code == 409
    assert r.json()["detail"]["kind"] == "not_confirmed"


def test_an_unchecked_confirmation_box_is_not_a_confirmation(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "steps")
    r = guided_env.post(f"/api/guided/{sid}/steps/confirm", json={"confirmed": False})
    assert r.status_code == 409
    assert r.json()["detail"]["kind"] == "not_confirmed"


def test_changing_the_basis_invalidates_everything_downstream(guided_env):
    """公式是对着旧依据核的。换了依据还留着那次确认，等于凭空背书。"""
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")
    assert guided_env.get(f"/api/guided/{sid}").json()["can_run"]

    guided_env.post(f"/api/guided/{sid}/basis", json={"basis_id": "b1"})
    body = guided_env.get(f"/api/guided/{sid}").json()
    assert not body["can_run"]
    assert body["steps"] == [] and body["inputs"] == []


def test_reproposing_steps_invalidates_the_previous_confirmation(guided_env):
    """用户确认的是"那一套"公式，不是"任意一套"。"""
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[GOOD_STEPS, GOOD_STEPS])
    sid = _walk_to(guided_env, "ready")
    assert guided_env.get(f"/api/guided/{sid}").json()["can_run"]
    guided_env.post(f"/api/guided/{sid}/steps")
    assert not guided_env.get(f"/api/guided/{sid}").json()["can_run"]


# ── 公式闸门：编出来的系数过不去 ───────────────────────────────────

def test_a_fabricated_coefficient_in_a_formula_is_refused(guided_env):
    """旧版最大的漏洞：`1.25 * F` 里那个曲度系数照样过闸。现在不行了。"""
    bad = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "4 * 1.25 * T * 1000 / (d * h * l)"},
        GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])

    sid = _walk_to(guided_env, "inputs_confirmed")
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("1.25" in x for x in detail["reasons"])
    assert any("做成 inputs 里的一项" in x for x in detail["reasons"])


def test_geometric_integers_and_unit_factors_are_allowed(guided_env):
    """闸门要拦编出来的系数，不该拦 `d ** 3` 里的 3 和 `* 1000`。"""
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "steps")
    body = guided_env.get(f"/api/guided/{sid}").json()
    assert body["repair_log"]["steps"][0]["passed"]


def test_a_source_ref_outside_the_confirmed_basis_is_refused(guided_env):
    """公式不能挂在一个用户没确认过的标准上。"""
    bad = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "source": {"ref": "GB/T 6391-2010 第 5 章"}},
        GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])
    sid = _walk_to(guided_env, "inputs_confirmed")
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    assert any("没有指向用户确认的依据" in x for x in r.json()["detail"]["reasons"])


@pytest.mark.parametrize("kind", ["table_lookup", "table_interp", "table_pick",
                                  "row_select", "round_to_series"])
def test_every_table_reading_step_kind_is_still_refused(guided_env, kind):
    bad = {**GOOD_STEPS, "steps": [
        {"id": "t", "kind": kind, "name_zh": "查表", "table": "made_up",
         "outputs": ["x"]},
        GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])
    sid = _walk_to(guided_env, "inputs_confirmed")
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    assert any(kind in x for x in r.json()["detail"]["reasons"])


def test_steps_without_a_check_are_refused(guided_env):
    bad = {**GOOD_STEPS, "steps": [GOOD_STEPS["steps"][0]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])
    sid = _walk_to(guided_env, "inputs_confirmed")
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    assert any("没有任何校核步骤" in x for x in r.json()["detail"]["reasons"])


# ── 参数闸门 ───────────────────────────────────────────────────────

def test_a_handbook_quantity_without_a_where_to_look_hint_is_refused(guided_env):
    bad = {"inputs": [
        {**GOOD_INPUTS["inputs"][0]},
        {"id": "K", "name_zh": "工况系数", "unit": "", "type": "number",
         "required": True, "round": 1, "domain": {"min": 1, "max": 3},
         "from_handbook": True, "hint": ""},
    ], "notes": ""}
    _script(basis=GOOD_BASIS, inputs=[bad, bad, bad])
    sid = _walk_to(guided_env, "basis_chosen")
    r = guided_env.post(f"/api/guided/{sid}/inputs")
    assert r.status_code == 422
    assert any("写明去哪本手册" in x for x in r.json()["detail"]["reasons"])


def test_more_than_six_questions_in_one_round_is_refused(guided_env):
    """SKILL.md 阶段 2 的规矩：单轮不超过 6 项。一次问太多，工程师会放弃。"""
    many = {"inputs": [
        {"id": f"x{i}", "name_zh": f"参数{i}", "unit": "", "type": "number",
         "required": True, "round": 1, "domain": {"min": 0, "max": 9},
         "from_handbook": False, "hint": "随便"} for i in range(7)], "notes": ""}
    _script(basis=GOOD_BASIS, inputs=[many, many, many])
    sid = _walk_to(guided_env, "basis_chosen")
    r = guided_env.post(f"/api/guided/{sid}/inputs")
    assert r.status_code == 422
    assert any("单轮不得超过 6 项" in x for x in r.json()["detail"]["reasons"])


def test_a_proposed_default_value_is_refused(guided_env):
    """阶段 2 只问"要什么"，不给"取多少"。默认值会被当成建议直接用掉。"""
    bad = {"inputs": [
        GOOD_INPUTS["inputs"][0],
        {**GOOD_INPUTS["inputs"][1], "default": 40},
    ], "notes": ""}
    _script(basis=GOOD_BASIS, inputs=[bad, bad, bad])
    sid = _walk_to(guided_env, "basis_chosen")
    r = guided_env.post(f"/api/guided/{sid}/inputs")
    assert r.status_code == 422
    assert any("填了 default" in x for x in r.json()["detail"]["reasons"])


# ── 修正循环 ───────────────────────────────────────────────────────

def test_non_compliant_output_is_sent_back_to_the_model_not_to_the_user(guided_env):
    """闸门拦下来之后甩给用户是最没用的做法 —— 用户不知道模型哪里写错了。"""
    bad = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "4 * 1.3 * T / (d * h * l)"},
        GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, GOOD_STEPS])

    sid = _walk_to(guided_env, "inputs_confirmed")
    before = len(_FAKE.calls)
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 200          # 用户根本不需要知道中间那一轮失败了

    log = r.json()["repair_log"]["steps"]
    assert len(log) == 2 and log[1]["passed"]
    # 第二次调用带着更长的对话（原提问 + 上一轮输出 + 修正指令）
    calls = _FAKE.calls[before:]
    assert len(calls) == 2
    assert calls[1]["turns"] > calls[0]["turns"]


def test_repair_runs_the_same_audit_and_never_relaxes_it(guided_env):
    """修三轮还是不合规，就该停在这里 —— 不存在"试到第三次就放行"。"""
    bad1 = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "1.25 * T"}, GOOD_STEPS["steps"][1]]}
    bad2 = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "0.615 * T"}, GOOD_STEPS["steps"][1]]}
    bad3 = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "2.5 * T"}, GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad1, bad2, bad3, GOOD_STEPS])

    sid = _walk_to(guided_env, "inputs_confirmed")
    before = len(_FAKE.calls)
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    # 默认 2 轮修正 = 最多 3 次调用，第 4 次那份合规的输出根本没机会用上
    assert len(_FAKE.calls[before:]) == 3
    assert len(r.json()["detail"]["repair_log"]) == 3


def test_repair_stops_early_when_the_reasons_stop_shrinking(guided_env):
    """问题清单没收窄说明它没在收敛，再跑就是白花用户的钱。"""
    same = {**GOOD_STEPS, "steps": [
        {**GOOD_STEPS["steps"][0], "expr": "1.25 * T"}, GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[same, same, same, same])

    sid = _walk_to(guided_env, "inputs_confirmed")
    before = len(_FAKE.calls)
    r = guided_env.post(f"/api/guided/{sid}/steps")
    assert r.status_code == 422
    # 第二轮的原因与第一轮完全相同 → 当场停，不跑第三轮
    assert len(_FAKE.calls[before:]) == 2
    assert r.json()["detail"]["repair_log"][-1].get("stopped")


def test_engine_never_patches_the_model_output_itself(guided_env):
    """引擎只把原因退回去，不自己删违规步骤 —— 它改出来的内容没有作者。"""
    bad = {**GOOD_STEPS, "steps": [
        {"id": "t", "kind": "table_lookup", "name_zh": "查表",
         "table": "made_up", "outputs": ["x"]},
        GOOD_STEPS["steps"][1]]}
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])
    sid = _walk_to(guided_env, "inputs_confirmed")
    assert guided_env.post(f"/api/guided/{sid}/steps").status_code == 422

    # 会话里没有留下"被清理过"的半份步骤
    body = guided_env.get(f"/api/guided/{sid}").json()
    assert body["steps"] == []
    assert not body["can_confirm_formulas"]


def test_user_sees_the_last_round_reasons_when_repair_is_exhausted(guided_env):
    bad = {**GOOD_STEPS, "steps": [GOOD_STEPS["steps"][0]]}   # 没有校核
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=[bad, bad, bad])
    sid = _walk_to(guided_env, "inputs_confirmed")
    detail = guided_env.post(f"/api/guided/{sid}/steps").json()["detail"]
    assert detail["stage"] == "steps"
    assert detail["reasons"]
    assert "没能给出合规的输出" in detail["error"] or "没能给出合规的输出" in str(detail)


# ── 端到端：跑起来、存下来、离线还能用 ─────────────────────────────

def test_guided_selection_runs_through_the_normal_engine(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")

    spec = guided_env.get(f"/api/guided/{sid}/spec").json()
    assert spec["provenance"] == "user_guided"
    assert spec["basis"]["claim"] == CLAIM
    assert spec["notes"][0].startswith("本流程由引导式选型组装")
    # 引导用的元数据不该漏进规格 —— spec.parse() 会拒未知字段
    assert all("from_handbook" not in i and "round" not in i for i in spec["inputs"])
    # 但"去哪查"必须留着，那是用户能把这个数填对的唯一线索
    assert any("【需查手册】" in (i.get("hint") or "") for i in spec["inputs"])


def test_saved_guided_material_is_selectable_offline(guided_env, client):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")

    out = guided_env.post(f"/api/guided/{sid}/save").json()
    assert out["saved"] and out["provenance"] == "user_guided"
    mid = out["material"]

    # 解绑账号 = 彻底离线。自建物料仍然可选、可跑。
    assert client.delete("/api/account").status_code == 200
    listing = {m["id"]: m for m in client.get("/api/materials").json()}
    assert mid in listing
    assert listing[mid]["provenance"] == "user_guided"
    assert listing[mid]["confidence"] == "unknown"

    r = client.post("/api/selection/run", json={
        "material": mid,
        "values": {"T": 50, "d": 40, "h": 8, "l": 56, "sigma_p": 110}})
    assert r.status_code == 200, r.text
    trace = r.json()["trace"]
    assert trace["status"] == "ok"
    assert trace["confidence"] == "unknown"
    warn = " ".join(trace["warnings"])
    assert "引导" in warn and CLAIM in warn
    assert "不等于经过核验" in warn


def test_saving_a_tampered_spec_is_refused(guided_env):
    """会话里待过二十分钟的规格不被信任 —— 落盘前用同一份判据再复核一遍。"""
    from server import drafts

    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")
    spec = guided_env.get(f"/api/guided/{sid}/spec").json()
    spec["steps"][0]["expr"] = "4 * 1.25 * T * 1000 / (d * h * l)"

    with pytest.raises(drafts.DraftError) as exc:
        drafts.save(spec)
    assert "1.25" in str(exc.value)


def test_the_saved_file_records_the_basis_it_was_built_on(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")
    out = guided_env.post(f"/api/guided/{sid}/save").json()

    from pathlib import Path
    text = Path(out["path"]).read_text(encoding="utf-8")
    assert "引导式选型" in text
    assert CLAIM in text
    assert MECHTOOL in text                    # 依据能点开核对
    assert "provenance: user_guided" in text


def test_a_session_survives_a_restart(guided_env):
    """一次引导要花二十分钟。用户去翻手册，回来不该发现进度归零。"""
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS)
    sid = _walk_to(guided_env, "inputs_confirmed")

    sess = guided.load(sid)                    # 绕过 HTTP，直接从库里读
    assert sess.stage == guided.STAGE_STEPS
    assert sess.basis["claim"] == CLAIM
    assert len(sess.inputs) == len(GOOD_INPUTS["inputs"])
    assert sid in [s["id"] for s in guided.listing()]


def test_a_session_can_be_discarded(guided_env):
    _script(basis=GOOD_BASIS)
    sid = _walk_to(guided_env, "basis")
    assert guided_env.delete(f"/api/guided/{sid}").json()["removed"]
    assert guided_env.get(f"/api/guided/{sid}").status_code == 404


def test_guided_is_closed_when_offline(client):
    """没有诚实的离线降级方案：阶段 1 要真的联网取证。"""
    r = client.post("/api/guided", json={"material_text": "磁吸铁片"})
    assert r.status_code == 409
    assert "联网" in r.json()["detail"]["message"]


def test_intent_points_at_guidance_not_drafting(guided_env):
    _script(intent={"material": None, "unknown_material": "磁吸铁片",
                    "values": {}, "unmatched": [], "notes": ""})
    body = guided_env.post("/api/ai/intent",
                           json={"text": "帮我选个磁吸铁片"}).json()
    assert body["unknown_material"] == "磁吸铁片"
    assert body["can_guide"] is True
    assert "can_draft" not in body


def test_a_guided_spec_runs_before_it_is_saved(guided_env):
    """"跑通了才存"的前提是落盘之前就能跑。

    一份没跑通的流程存下来，只会在物料列表里留一个点进去就报错的入口。
    """
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "ready")

    wf = guided_env.get(f"/api/guided/{sid}/workflow").json()
    assert wf["provenance"] == "user_guided"
    assert wf["confidence"] == "unknown"
    assert [i["id"] for i in wf["inputs"]] == ["T", "d", "h", "l", "sigma_p"]

    r = guided_env.post(f"/api/guided/{sid}/run", json={
        "values": {"T": 50, "d": 40, "h": 8, "l": 56, "sigma_p": 110}})
    assert r.status_code == 200
    trace = r.json()["trace"]
    assert trace["status"] == "ok"
    assert "不等于经过核验" in " ".join(trace["warnings"])

    # 跑完了，但还没存 —— 物料列表里不该有它
    ids = [m["id"] for m in guided_env.get("/api/materials").json()]
    assert "magnet_plate" not in ids


def test_the_workflow_endpoint_refuses_before_the_formulas_are_confirmed(guided_env):
    _script(basis=GOOD_BASIS, inputs=GOOD_INPUTS, steps=GOOD_STEPS)
    sid = _walk_to(guided_env, "steps")
    assert guided_env.get(f"/api/guided/{sid}/workflow").status_code == 409
    assert guided_env.post(f"/api/guided/{sid}/run",
                           json={"values": {}}).status_code == 409

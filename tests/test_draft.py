"""知识库里没有的物料：AI 起草 → 跑通 → 保存，下次离线可选。

这组测试守的是**那条一线之隔**：AI 生成流程可以，生成数据不行。

用户的原始诉求是"在线时应该能选任何物料"，而这个项目的立身之本是
"任何一个来路不明的数字都是 bug"。两者能同时成立，靠的是分工：

    引擎做算术（确定的事）→ AI 给流程（不确定的事）→ 数值由用户自己填

所以测试重点不在"能不能生成"，而在：

1. **两道闸门真的挡得住** —— 带数据表的草稿必须被拒，不是靠提示词劝退
2. **起草的东西全程打 🔴** —— 物料列表、trace、报告、导出件，一处都不能漏
3. **随包物料不会被顶掉** —— 生成的规格不得覆盖已核验的工作流
4. **保存后离线真的能用** —— 否则"下次离线可选"就是句空话
"""

from __future__ import annotations

import json

import pytest

from conftest import FAKE_KEY, FakeProvider


# 一份合规的草稿：所有需要查手册的量都是输入项，没有任何 table 引用
GOOD_DRAFT = {
    "material": "magnetic_plate",
    "name_zh": "磁吸铁片",
    "standard": "",
    "notes": ["依据待用户确认"],
    "inputs": [
        {"id": "F_hold", "name_zh": "所需吸持力", "unit": "N",
         "required": True, "domain": {"min": 1, "max": 100000},
         "hint": "按被吸持件重量与安全系数确定"},
        {"id": "B", "name_zh": "磁感应强度", "unit": "T",
         "required": True, "domain": {"min": 0.1, "max": 2.0},
         "hint": "查磁铁厂商样本的剩磁 Br，或用高斯计实测"},
        {"id": "mu0", "name_zh": "真空磁导率", "unit": "H/m",
         "required": True, "default": 0.000001256,
         "domain": {"min": 1e-7, "max": 1e-5},
         "hint": "物理常数 4π×10⁻⁷，一般不用改"},
        {"id": "A_allow", "name_zh": "可用贴合面积", "unit": "mm²",
         "required": True, "domain": {"min": 1, "max": 1000000},
         "hint": "由安装空间决定"},
    ],
    "steps": [
        {"id": "A_req", "kind": "formula", "name_zh": "所需吸合面积",
         "expr": "2 * mu0 * F_hold / (B ** 2) * 1000000", "unit": "mm²",
         "source": {"ref": "麦克斯韦吸力公式 F = B²A/(2μ₀)，待核"},
         "outputs": ["A_req"]},
        {"id": "chk_area", "kind": "check", "name_zh": "贴合面积校核",
         "value": "A_req", "op": "<=", "limit": "A_allow", "unit": "mm²",
         "on_fail": "增大贴合面积、改用剩磁更高的磁铁，或降低吸持力要求",
         "source": {"ref": "A_req ≤ A_allow"}},
    ],
    "result": [
        {"label": "所需吸合面积", "value": "{A_req}", "unit": "mm²"},
        {"label": "采购关键字", "value": "磁吸铁片 面积{A_req}"},
    ],
    "procure": {"material_template": "generic", "channels": ["taobao"],
                "fields": {"kind": "磁吸铁片"}},
    "confidence_note": "吸力公式为理想化模型，未计气隙与漏磁",
}


def _draft_reply(payload: dict):
    """让假 provider 把这份草稿当作起草结果吐回来。"""
    FakeProvider.script["draft"] = payload


@pytest.fixture(autouse=True)
def _wire_draft(monkeypatch):
    """给 FakeProvider 接上起草剧本。

    真的 provider 按 system prompt 分流；这里按同样的方式挂一条分支，
    免得测试跑在一条真实环境里不存在的路径上。
    """
    orig = FakeProvider.chat

    def chat(self, messages, *, model, json_mode=False, max_tokens=800, temperature=0.2):
        if "工作流起草模块" in messages[0]["content"]:
            from server.ai.client import Completion, Usage
            FakeProvider.calls.append({"model": model, "json_mode": json_mode,
                                       "max_tokens": max_tokens})
            body = FakeProvider.script.get("draft", GOOD_DRAFT)
            return Completion(text=json.dumps(body, ensure_ascii=False),
                              usage=Usage(200, 400, 600, 0, model))
        return orig(self, messages, model=model, json_mode=json_mode,
                    max_tokens=max_tokens, temperature=temperature)

    monkeypatch.setattr(FakeProvider, "chat", chat)
    FakeProvider.script.pop("draft", None)


# --- 1. 原来的死胡同必须消失 -----------------------------------------------

def test_unknown_material_is_not_a_dead_end(client, bind_as):
    """用户的原始抱怨：「给定物料清单中无对应磁吸铁片的物料，无法完成选型解析」。

    认不出来不该是终点——要把物料名带回去，并告诉前端这条路走得通。"""
    bind_as("deepseek")
    FakeProvider.script["intent"] = {
        "material": None, "unknown_material": "磁吸铁片",
        "values": {}, "unmatched": [], "notes": ""}
    r = client.post("/api/ai/intent", json={"text": "帮我选个磁吸铁片"}).json()

    assert r["material"] is None
    assert r["unknown_material"] == "磁吸铁片"
    assert r["can_draft"] is True, "在线且已绑定时，必须给出起草这条路"


def test_offline_says_it_cannot_draft_rather_than_offering_a_dead_button(client):
    """离线时起草确实做不到。要说清楚，但 can_draft 必须是 False——
    给一个点了没反应的按钮比不给更糟。"""
    r = client.post("/api/ai/intent", json={"text": "帮我选个磁吸铁片"}).json()
    assert r["material"] is None
    assert r["can_draft"] is False
    assert "绑定" in r["notes"]


def test_draft_requires_being_online(client):
    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_bound"


# --- 2. 两道闸门（最要紧的一组）---------------------------------------------

def test_draft_carrying_a_data_table_is_rejected(client, bind_as):
    """闸门二。**这是整个特性的核心约束。**

    新物料没有任何数据表，一个引用了表的草稿只有两种可能：
    引用不存在的表（跑起来就崩），或者模型顺手编了一张表的内容。
    两种都不能放行。"""
    bind_as("deepseek")
    bad = json.loads(json.dumps(GOOD_DRAFT))
    bad["steps"].insert(0, {
        "id": "K_A", "kind": "table_lookup", "name_zh": "工况系数",
        "table": "condition_factors", "key": "factors.{duty}",
        "outputs": ["K_A"]})
    _draft_reply(bad)

    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "draft_rejected"
    assert any("数据表" in x for x in detail["reasons"])


@pytest.mark.parametrize("kind", [
    "table_lookup", "table_interp", "table_pick", "row_select", "round_to_series"])
def test_every_table_reading_step_kind_is_refused(client, bind_as, kind):
    """五种查表步骤一个都不能漏。漏掉一种，就有一条绕过闸门的路。"""
    bind_as("deepseek")
    bad = json.loads(json.dumps(GOOD_DRAFT))
    bad["steps"].insert(0, {"id": "x", "kind": kind, "name_zh": "查点什么",
                            "outputs": ["x"]})
    _draft_reply(bad)
    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 422, f"{kind} 没被挡住"


def test_draft_without_any_check_is_rejected(client, bind_as):
    """只有算术没有校核，那不是选型。"""
    bind_as("deepseek")
    bad = json.loads(json.dumps(GOOD_DRAFT))
    bad["steps"] = [s for s in bad["steps"] if s["kind"] != "check"]
    _draft_reply(bad)
    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 422
    assert any("校核" in x for x in r.json()["detail"]["reasons"])


def test_formula_without_a_source_is_rejected(client, bind_as):
    """每个公式都要说明出处。说不出来的公式和编的没区别。"""
    bind_as("deepseek")
    bad = json.loads(json.dumps(GOOD_DRAFT))
    bad["steps"][0].pop("source")
    _draft_reply(bad)
    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 422
    assert any("source" in x for x in r.json()["detail"]["reasons"])


def test_malformed_draft_is_caught_by_the_engine_parser(client, bind_as):
    """闸门一：与随包工作流走完全同一个解析器。
    模型编不出一个"只对它自己成立"的格式。"""
    bind_as("deepseek")
    bad = json.loads(json.dumps(GOOD_DRAFT))
    bad["steps"][0]["expr"] = "A_req * 不存在的变量"
    _draft_reply(bad)
    r = client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    assert r.status_code == 422


def test_draft_cannot_shadow_a_builtin_material(client, bind_as):
    """生成的规格不得顶掉已核验的随包工作流。"""
    bind_as("deepseek")
    shadow = json.loads(json.dumps(GOOD_DRAFT))
    shadow["material"] = "bolt"
    _draft_reply(shadow)
    out = client.post("/api/ai/draft-workflow",
                      json={"material_text": "螺栓"}).json()
    assert out["material"] != "bolt", "重名必须改掉"
    assert out["material"].startswith("bolt")


# --- 3. 成功路径 -------------------------------------------------------------

def test_good_draft_passes_and_is_marked(client, bind_as):
    bind_as("deepseek")
    out = client.post("/api/ai/draft-workflow",
                      json={"material_text": "磁吸铁片"}).json()
    assert out["material"] == "magnetic_plate"
    assert out["checks"] >= 1
    assert out["spec"]["provenance"] == "ai_generated"
    assert out["spec"]["generated_by"]
    # notes 第一条必须是"未经核验"的声明，且要说清引擎保证了什么、没保证什么
    first = out["spec"]["notes"][0]
    assert "未经" in first and "核验" in first


def test_draft_is_not_saved_until_asked(client, bind_as):
    """起草成功不等于落盘。一份没跑通的流程存下来，
    只会在物料列表里留一个点进去就报错的入口。"""
    bind_as("deepseek")
    client.post("/api/ai/draft-workflow", json={"material_text": "磁吸铁片"})
    ids = {m["id"] for m in client.get("/api/materials").json()}
    assert "magnetic_plate" not in ids


# --- 4. 保存之后：离线可选（用户诉求的后半段）-------------------------------

def test_saved_material_becomes_selectable_and_works_offline(client, bind_as):
    bind_as("deepseek")
    draft = client.post("/api/ai/draft-workflow",
                        json={"material_text": "磁吸铁片"}).json()

    saved = client.post("/api/ai/save-draft", json={"spec": draft["spec"]})
    assert saved.status_code == 200, saved.text

    mats = {m["id"]: m for m in client.get("/api/materials").json()}
    assert "magnetic_plate" in mats
    assert mats["magnetic_plate"]["status"] == "ready"
    assert mats["magnetic_plate"]["provenance"] == "ai_generated"
    # 置信度按最低档 —— 它确实没有任何信源
    assert mats["magnetic_plate"]["confidence"] == "unknown"

    # **离线跑通**：这是"下次离线也能选"这句话的实证
    client.delete("/api/account")
    assert client.get("/api/account").json()["bound"] is False

    body = client.post("/api/selection/run", json={
        "material": "magnetic_plate", "save": False,
        "values": {"F_hold": 500, "B": 1.2, "mu0": 0.000001256,
                   "A_allow": 5000}}).json()
    assert body["trace"]["status"] in ("ok", "check_failed")
    assert body["trace"]["steps"], "离线跑不出步骤，等于没保存"


def test_result_from_a_generated_workflow_carries_the_warning(client, bind_as):
    """🔴 标记要跟到 trace 里 —— 报告与导出件都从 trace 取警告。"""
    bind_as("deepseek")
    draft = client.post("/api/ai/draft-workflow",
                        json={"material_text": "磁吸铁片"}).json()
    client.post("/api/ai/save-draft", json={"spec": draft["spec"]})

    trace = client.post("/api/selection/run", json={
        "material": "magnetic_plate", "save": False,
        "values": {"F_hold": 500, "B": 1.2, "mu0": 0.000001256,
                   "A_allow": 5000}}).json()["trace"]

    assert trace["confidence"] == "unknown"
    blob = " ".join(trace["warnings"])
    assert "AI 起草" in blob
    assert "不要拿这份结果直接定稿" in blob


def test_saving_a_tampered_spec_is_refused(client, bind_as):
    """落盘前再独立校验一遍——不信任调用方，也不信任草稿在内存里待过一段时间。"""
    bind_as("deepseek")
    draft = client.post("/api/ai/draft-workflow",
                        json={"material_text": "磁吸铁片"}).json()
    tampered = json.loads(json.dumps(draft["spec"]))
    tampered["steps"].append({
        "id": "sneak", "kind": "table_lookup", "name_zh": "偷偷查个表",
        "table": "whatever", "key": "a.b", "outputs": ["z"]})

    r = client.post("/api/ai/save-draft", json={"spec": tampered})
    assert r.status_code == 422


def test_saved_material_can_be_removed(client, bind_as):
    bind_as("deepseek")
    draft = client.post("/api/ai/draft-workflow",
                        json={"material_text": "磁吸铁片"}).json()
    client.post("/api/ai/save-draft", json={"spec": draft["spec"]})

    assert client.delete("/api/ai/saved/magnetic_plate").status_code == 200
    ids = {m["id"] for m in client.get("/api/materials").json()}
    assert "magnetic_plate" not in ids


def test_builtin_material_cannot_be_deleted_through_this_door(client, bind_as):
    bind_as("deepseek")
    r = client.delete("/api/ai/saved/bolt")
    assert r.status_code == 404
    assert "bolt" in {m["id"] for m in client.get("/api/materials").json()}

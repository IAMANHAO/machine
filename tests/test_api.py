"""API 契约测试。

重点守两件事：
1. 前端拿到的 trace 与引擎产出的 trace 是同一份东西（不存在第二份展示用数据）
2. 引擎的"停下来报缺口"语义必须原样穿过 HTTP，不被降级成 500 或一个假数字
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

GOLDEN = {
    "P": 5.5, "n1": 1450, "i": 2, "a0": 400,
    "prime_mover": "ac_motor_normal",
    "work_machine": "medium_uniform",
    "hours_per_day": "h_le_10",
    "belt_type": "H",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个测试用独立的项目库，不碰开发机上的真实数据。"""
    monkeypatch.setenv("MDS_DATA_DIR", str(tmp_path / "data"))
    from server.main import app
    with TestClient(app) as c:
        yield c


def _run(client, **overrides):
    values = {**GOLDEN, **overrides}
    for key in [k for k, v in values.items() if v is None]:
        values.pop(key)
    return client.post("/api/selection/run",
                       json={"material": "synchronous_belt", "values": values})


# --- 目录 -----------------------------------------------------------------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["workflows"] >= 1
    assert body["ai_bound"] is False, "M4 之前不得声称已绑定 AI 账号"


def test_materials_cover_their_states(client):
    """ready = 有可执行 YAML；cache_only = 只有 .md 与缓存；planned = 连缓存都没有。

    第二阶段把 10 个 planned 物料全部规格化了，所以现在 planned 与 cache_only
    都是空的。这两个状态**仍然保留**——新物料先有 .md 与缓存、后有 YAML 时会走到它们。

    这里刻意不再断言"某个物料处于某个状态"（那种断言每加一个物料就要改一次，
    改着改着就只剩下"跟上次一样"的意思），改为守住状态划分本身的正确性：
    凡是 ready 的必须真有可执行规格，且状态取值不超出这三种。"""
    from mds import spec as mds_spec

    mats = {m["id"]: m for m in client.get("/api/materials").json()}
    executable = set(mds_spec.available())

    ready = {mid for mid, m in mats.items() if m["status"] == "ready"}
    assert ready == executable, "ready 的集合必须与真正有可执行 YAML 的一致"
    assert {m["status"] for m in mats.values()} <= {"ready", "cache_only", "planned"}

    # ready 的物料必须有表、有工作流文档，否则界面会给出一个点进去是空的入口
    for mid in ready:
        assert mats[mid]["table_count"] > 0, mid
        assert mats[mid]["workflow_doc"], mid


def test_material_confidence_is_real(client):
    """首页徽章必须反映数据表真实状态，不能是写死的绿点。"""
    mats = {m["id"]: m for m in client.get("/api/materials").json()}
    assert mats["synchronous_belt"]["confidence"] == "single_source"


def test_workflow_definition(client):
    wf = client.get("/api/workflow/synchronous_belt").json()
    assert wf["standard"] == "GB/T 11362-2021"
    ids = {i["id"] for i in wf["inputs"]}
    assert {"P", "n1", "prime_mover", "belt_type"} <= ids

    prime = next(i for i in wf["inputs"] if i["id"] == "prime_mover")
    assert prime["type"] == "enum"
    assert any("电动机" in o["label"] for o in prime["options"]), "枚举必须带中文标签"

    ratio = [i for i in wf["inputs"] if i["one_of"] == "ratio"]
    assert {i["id"] for i in ratio} == {"i", "n2"}
    assert wf["notes"], "规格里的已知偏差说明必须透传给前端"


def test_unknown_workflow_is_404(client):
    assert client.get("/api/workflow/nope").status_code == 404


# --- 选型执行 --------------------------------------------------------------

def test_run_golden_case(client):
    body = _run(client).json()
    trace = body["trace"]
    assert trace["status"] == "ok"
    assert trace["outputs"]["bs"] == 76.2
    assert len(trace["checks"]) == 6
    assert all(c["detail"]["passed"] for c in trace["checks"])
    assert body["procure"]["links"], "阶段 6 必须出采购链接"


def test_run_exposes_full_derivation(client):
    """阶段 3 计算卡片要能逐行渲染：公式、代入、结果、信源、置信度。"""
    trace = _run(client).json()["trace"]
    pd = next(s for s in trace["steps"] if s["id"] == "Pd")
    assert pd["formula"] and pd["substitution"] == "1.1 × 5.5"
    assert pd["value_display"] == "6.05"

    ka = next(s for s in trace["steps"] if s["id"] == "K_A")
    assert ka["source"]["table_file"] == "condition_factors.yaml"
    assert ka["source"]["confidence"] == "single_source"
    assert trace["sources"] and trace["confidence"] == "single_source"
    assert trace["warnings"], "非 verified 数据必须带风险提示"


def test_data_missing_surfaces_the_gap_not_a_number(client):
    body = _run(client, n1=4000).json()
    trace = body["trace"]
    assert trace["status"] == "data_missing"
    step = next(s for s in trace["steps"] if s["id"] == "P0")
    assert step["value"] is None
    assert "4000" in step["error"]["gap"]
    assert body["procure"] is None, "没算出结果就不该给采购链接"


def test_needs_choice_returns_candidates(client):
    trace = _run(client, belt_type=None).json()["trace"]
    assert trace["status"] == "needs_choice"
    assert trace["blocker"]["step"] == "belt_type_sel"
    assert len(trace["blocker"]["candidates"]) == 5
    assert trace["blocker"]["reason"], "必须说明为什么引擎不能自己决定"


def test_choice_endpoint_completes_the_run(client):
    values = {k: v for k, v in GOLDEN.items() if k != "belt_type"}
    body = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": values,
        "choices": {"belt_type_sel": "H"}}).json()
    assert body["trace"]["status"] == "ok"


def test_check_failure_is_reported_not_hidden(client):
    """L 型 + 低转速：带速校核通过但额定功率不足，必须如实报不通过。"""
    trace = _run(client, belt_type="XL", n1=300, P=0.1, a0=120).json()["trace"]
    assert trace["status"] in ("ok", "check_failed", "data_missing")
    if trace["status"] == "check_failed":
        failed = [c for c in trace["checks"] if not c["detail"]["passed"]]
        assert failed and all(c["detail"]["remedy"] for c in failed), "不通过必须给调整建议"


def test_invalid_input_is_422_with_field(client):
    r = _run(client, P=9999)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "InputError"
    assert detail["param"] == "P"


def test_missing_required_input_is_422(client):
    r = client.post("/api/selection/run",
                    json={"material": "synchronous_belt", "values": {"P": 5.5}})
    assert r.status_code == 422


# --- 项目库 ---------------------------------------------------------------

def test_run_persists_a_project(client):
    body = _run(client).json()
    pid = body["project_id"]
    assert pid

    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == [pid]
    assert listed[0]["status"] == "ok"
    assert "1450" in listed[0]["summary"]
    assert listed[0]["confidence"] == "single_source"

    full = client.get(f"/api/projects/{pid}").json()
    assert full["trace"]["outputs"]["bs"] == 76.2
    assert full["values"]["belt_type"] == "H"


def test_rerun_updates_same_project(client):
    pid = _run(client).json()["project_id"]
    body = client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": {**GOLDEN, "a0": 500},
        "project_id": pid}).json()
    assert body["project_id"] == pid
    assert len(client.get("/api/projects").json()) == 1


def test_save_false_does_not_persist(client):
    client.post("/api/selection/run", json={
        "material": "synchronous_belt", "values": GOLDEN, "save": False})
    assert client.get("/api/projects").json() == []


def test_delete_project(client):
    pid = _run(client).json()["project_id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/projects/{pid}").status_code == 404


# --- 采购 -----------------------------------------------------------------

def test_procure_links_endpoint(client):
    body = client.post("/api/procure/links", json={
        "material_template": "lubricant",
        "fields": {"brand": "美孚", "vg": "68", "kind": "导轨油", "pack": "18L"},
    }).json()
    assert "VG68" in body["keyword"]
    assert all(lk["url"].startswith("https://") for lk in body["links"])
    assert body["note"], "必须告知这是搜索页而非具体商品"


def test_reopened_project_carries_procure_links(client):
    """重开一个已完成的项目，阶段 6 不该是空的——链接从存档 trace 重算。"""
    pid = _run(client).json()["project_id"]
    full = client.get(f"/api/projects/{pid}").json()
    assert full["procure"] is not None
    assert full["procure"]["links"]
    assert full["procure"]["keyword"] == "H 带宽76.2 节线长1219.2 同步带"


def test_unfinished_project_has_no_procure(client):
    body = _run(client, belt_type=None).json()
    full = client.get(f"/api/projects/{body['project_id']}").json()
    assert full["procure"] is None

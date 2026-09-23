"""知识库 API 测试（M3）。

重点守三件事：
1. 🟢 必须有第二信源才能打上去——假绿灯比没有绿灯更危险
2. 写入不能吃掉数据表里的注释（那里面是公式和录入规则）
3. 写完之后引擎立刻看到新数据，界面与计算不会各说各话
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

MATERIAL = "synchronous_belt"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把整个 skill 数据目录复制一份，测试只在副本上改。"""
    from server.config import DEFAULT_SKILL_ROOT

    root = tmp_path / "skill"
    shutil.copytree(DEFAULT_SKILL_ROOT, root,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.bak"))
    monkeypatch.setenv("MDS_SKILL_ROOT", str(root))
    monkeypatch.setenv("MDS_DATA_DIR", str(tmp_path / "data"))

    # config.skill_root 每次读环境变量，但引擎的 Knowledge 实例有缓存
    from server import engine
    engine.reset_caches()
    from mds import knowledge as mds_knowledge
    monkeypatch.setattr(mds_knowledge, "SKILL_ROOT", root)
    from mds import editor as mds_editor
    monkeypatch.setattr(mds_editor, "SKILL_ROOT", root)
    engine.reset_caches()

    from server.main import app
    with TestClient(app) as c:
        yield c, root
    engine.reset_caches()


@pytest.fixture()
def client(sandbox):
    return sandbox[0]


@pytest.fixture()
def root(sandbox):
    return sandbox[1]


def _table_file(root, name: str):
    return root / "knowledge" / "cache" / MATERIAL / f"{name}.yaml"


# --- 自检 ------------------------------------------------------------------

def test_audit_reports_real_state(client):
    """自检汇总必须反映**真实**状态。

    这里刻意不写死表的张数——每加一个物料就改一次数字，
    改着改着这条断言就只剩下"跟上次一样"的意思了。
    真正该守的是：汇总数与引擎实际持有的表对得上，
    没有假绿灯，且确实存在算不出来的工况。"""
    from mds import Knowledge
    know = Knowledge()
    real = sum(len(know.tables_of(m)) for m in know.materials())

    body = client.get("/api/knowledge/audit").json()
    t = body["totals"]
    assert t["tables"] == real, "汇总的表数与引擎实际持有的对不上"
    assert t["verified"] == 0, "目前没有任何一张表完成双源核验"
    assert t["probed"] > 0 and t["reachable"] < t["probed"], "应当存在算不出来的工况"


def test_audit_probe_runs_the_real_engine(client):
    body = client.get(f"/api/knowledge/{MATERIAL}/audit").json()
    matrix = body["probe"]["matrix"]
    assert len(matrix) == 15
    ok = [m for m in matrix if m["status"] == "ok"]
    assert {(m["combo"]["belt_type"], m["combo"]["n1"]) for m in ok} == {("H", 1450), ("H", 2880)}


def test_gaps_distinguish_missing_data_from_no_solution(client):
    gaps = client.get(f"/api/knowledge/{MATERIAL}/audit").json()["gaps"]
    kinds = {g["kind"] for g in gaps}
    assert "unreachable" in kinds, "应当报出数据缺失导致的不可达工况"
    assert "no_solution" in kinds, "系列内无解要单独归类，不能混进数据缺口"

    no_sol = [g for g in gaps if g["kind"] == "no_solution"]
    assert all(g["severity"] == "warning" for g in no_sol), "无解不是阻断问题"
    assert all("补" not in g["fix_hint"] for g in no_sol), "无解不该提示去补数据"


def test_gaps_carry_fill_targets(client):
    gaps = client.get(f"/api/knowledge/{MATERIAL}/audit").json()["gaps"]
    fillable = [g for g in gaps if g["target"]]
    assert fillable, "数据类缺口必须带上补录坐标，否则界面没法一键跳过去"
    t = fillable[0]["target"]
    assert {"table", "group", "x_field", "x_value", "y_field", "z_field"} <= set(t)
    assert t["y_missing"], "要说清缺的是哪几个点"


# --- 双源核验 --------------------------------------------------------------

def test_verify_requires_second_source(client):
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/verify",
                    json={"second_source": ""})
    assert r.status_code == 422
    assert "第二信源" in r.json()["detail"]["message"]


def test_verify_rejects_same_source(client):
    tbl = client.get(f"/api/knowledge/{MATERIAL}/tables/belt_pitch").json()
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/verify",
                    json={"second_source": tbl["meta"]["data_source"]})
    assert r.status_code == 422
    assert "交叉验证" in r.json()["detail"]["message"]


def test_verify_succeeds_with_independent_second_source(client, root):
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/verify",
                    json={"second_source": "成大先《机械设计手册》第3卷 第14篇（纸质原件核对）",
                          "verified_by": "测试"})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "verified"

    after = client.get(f"/api/knowledge/{MATERIAL}/tables/belt_pitch").json()
    assert after["confidence"] == "verified"
    assert after["meta"]["second_source"]
    assert "_todo" not in after["meta"], "核验通过后待办应当清掉"

    audit = client.get(f"/api/knowledge/{MATERIAL}/audit?probe=false").json()
    entry = next(t for t in audit["tables"] if t["name"] == "belt_pitch")
    assert entry["issues"] == [], "有第二信源的 verified 不应再被报为假绿灯"


def test_verified_table_lifts_selection_confidence(client):
    """核验状态要真的流到选型结果里，不能只是知识库页自己好看。"""
    before = client.post("/api/selection/run", json={
        "material": MATERIAL, "save": False,
        "values": {"P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
                   "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
                   "hours_per_day": "h_le_10"}}).json()
    src = next(s for s in before["trace"]["sources"] if s["table_file"] == "belt_pitch.yaml")
    assert src["confidence"] == "single_source"

    client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/verify",
                json={"second_source": "成大先《机械设计手册》第3卷（纸质原件核对）"})

    after = client.post("/api/selection/run", json={
        "material": MATERIAL, "save": False,
        "values": {"P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
                   "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
                   "hours_per_day": "h_le_10"}}).json()
    src2 = next(s for s in after["trace"]["sources"] if s["table_file"] == "belt_pitch.yaml")
    assert src2["confidence"] == "verified"
    assert src2["second_source"]


def test_unverify_returns_to_single_source(client):
    client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/verify",
                json={"second_source": "另一本手册"})
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/unverify",
                    json={"reason": "发现 XH 的 vmax 对不上"})
    assert r.status_code == 200
    after = client.get(f"/api/knowledge/{MATERIAL}/tables/belt_pitch").json()
    assert after["confidence"] == "single_source"
    assert "对不上" in str(after["meta"]["_todo"])


# --- 数据录入 --------------------------------------------------------------

def test_upsert_points_preserves_comments(client, root):
    """数据表里的注释是公式与录入规则，写入绝不能把它们吃掉。"""
    path = _table_file(root, "rated_power")
    before = sum(1 for ln in path.read_text(encoding="utf-8").splitlines()
                 if ln.strip().startswith("#"))

    r = client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_H", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450,
        "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 20, "P0": 1.78}]})
    assert r.status_code == 200 and r.json()["written"] == 1

    after = sum(1 for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip().startswith("#"))
    assert after == before, f"注释从 {before} 行掉到 {after} 行"


def test_upsert_points_is_visible_to_the_engine_immediately(client):
    """写完之后引擎必须立刻看到新数据，否则界面与计算会各说各话。"""
    gaps_before = client.get(f"/api/knowledge/{MATERIAL}/audit").json()["gaps"]
    xxh = [g for g in gaps_before
           if g["target"].get("group") == "belt_type_XXH" and g["target"].get("x_value") == 1450]
    assert xxh, "XXH 在 n1=1450 应当有缺口"

    client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_XXH", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450,
        "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 28, "P0": 7.4}]})

    cell = next(m for m in client.get(f"/api/knowledge/{MATERIAL}/audit").json()["probe"]["matrix"]
                if m["combo"]["belt_type"] == "XXH" and m["combo"]["n1"] == 1450)
    assert cell["status"] != "data_missing", "补了数据之后该工况不应再报数据缺失"


def test_editing_a_verified_table_revokes_the_green_light(client):
    """改过的数据不能继续顶着核验过的绿灯。"""
    client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/verify",
                json={"second_source": "GB/T 11362-2021 纸质原件"})
    assert client.get(f"/api/knowledge/{MATERIAL}/tables/rated_power").json()["confidence"] == "verified"

    client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_H", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450, "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 20, "P0": 1.78}]})

    after = client.get(f"/api/knowledge/{MATERIAL}/tables/rated_power").json()
    assert after["confidence"] == "single_source"
    assert "重新" in str(after["meta"]["_todo"])


def test_upsert_rejects_non_numeric(client):
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_H", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450, "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 20, "P0": "很大"}]})
    assert r.status_code == 422


def test_upsert_rejects_unknown_group(client):
    r = client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_ZZZ", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450, "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 20, "P0": 1.0}]})
    assert r.status_code == 400


# --- 存档失效 --------------------------------------------------------------

def test_project_flags_stale_sources_after_data_edit(client):
    """数据表改过之后，旧项目要提示"这是旧数据算出来的"。"""
    pid = client.post("/api/selection/run", json={
        "material": MATERIAL,
        "values": {"P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
                   "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
                   "hours_per_day": "h_le_10"}}).json()["project_id"]

    assert client.get(f"/api/projects/{pid}").json()["stale_sources"] == []

    client.post(f"/api/knowledge/{MATERIAL}/tables/rated_power/points", json={
        "group": "belt_type_H", "rows_key": "examples",
        "x_field": "n1", "x_value": 1450, "y_field": "z1", "z_field": "P0",
        "points": [{"z1": 20, "P0": 1.78}]})

    stale = client.get(f"/api/projects/{pid}").json()["stale_sources"]
    assert [s["table_file"] for s in stale] == ["rated_power.yaml"]
    assert stale[0]["reason"] == "数据表已更新"


def test_write_preserves_two_document_layout(client, root):
    """写回后文件必须仍是 ---/frontmatter/---/正文 的两段式，且开头保留 ---。

    ruamel 默认不给第一个文档写显式起始标记，会让这个文件与其余缓存文件不一致。
    """
    path = _table_file(root, "belt_pitch")
    client.patch(f"/api/knowledge/{MATERIAL}/tables/belt_pitch/sources",
                 json={"note": "回归测试"})

    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "开头的 --- 不能丢"
    assert text.count("\n---\n") >= 1, "frontmatter 与正文之间的分隔必须保留"

    import yaml
    docs = [d for d in yaml.safe_load_all(text) if d]
    assert len(docs) == 2, f"应当仍是两个文档，实际 {len(docs)}"
    assert "data_source" in docs[0] and "belt_types" in docs[1]

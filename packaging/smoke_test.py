#!/usr/bin/env python3
"""打包产物冒烟测试 —— 起真正的 exe，走真正的流程。

为什么必须单独有这一层：下面每一条都是源码态**测不出来**的问题，
只有在打包产物上跑才会暴露。这次构建就实际踩了三个：

- mds.knowledge 用 __file__ 推数据根 → 打包后指到归档里，工作流数 0
- mds.procure 运行时按路径 import scripts/procure_link.py → 包里没有
- spec 里把 PIL 排进 excludes → reportlab 硬依赖它，PDF 导出 500

    python packaging/smoke_test.py [--exe dist/mds-server/mds-server.exe]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = ROOT / "dist" / "mds-server" / "mds-server.exe"

GOLDEN = {
    "P": 5.5, "n1": 1450, "i": 2, "a0": 400, "belt_type": "H",
    "prime_mover": "ac_motor_normal", "work_machine": "medium_uniform",
    "hours_per_day": "h_le_10",
}

# 冒烟用的自建物料。刻意写成完整的两段：**这就是引导式保存下来的形状**，
# 包括那条依据（basis）—— 它是引导式与旧版「AI 一次性起草」最大的区别。
SMOKE_SPEC = """material: smoke_probe_material
name_zh: 冒烟测试物料
provenance: user_guided
generated_by: smoke-test
basis:
  claim: 冒烟测试用的假依据（GB/T 0000）
  status: self_declared
  confirmed_at: '2026-01-01T00:00:00+00:00'
  confirmed_by: user
  urls: []
notes:
  - 冒烟测试用，跑完即删
inputs:
  - id: F
    name_zh: 载荷
    unit: N
    required: true
    domain: {min: 1, max: 1000}
    hint: 冒烟测试
  - id: A
    name_zh: 许用面积
    unit: mm2
    required: true
    domain: {min: 1, max: 1000}
    hint: 冒烟测试
steps:
  - id: s
    kind: formula
    name_zh: 算个数
    expr: F * 2
    unit: mm2
    source: {ref: 冒烟测试}
    outputs: [s]
  - id: c
    kind: check
    name_zh: 校核
    value: s
    op: <=
    limit: A
    unit: mm2
    on_fail: 加大面积
    source: {ref: 冒烟测试}
result:
  - label: 结果
    value: '{s}'
    unit: mm2
"""


def _user_workflow_dir():
    """打包态的用户工作流目录。

    **打包态 MDS_DATA_DIR 不是环境变量**，是从 LOCALAPPDATA 算出来的——
    这正是上一轮踩到的那个 bug 的现场（引擎读不到它，草稿存得进去却看不见）。
    这里按服务端同一套规则算一遍。
    """
    import os

    raw = os.environ.get("MDS_DATA_DIR")
    if raw:
        return Path(raw).expanduser() / "workflows"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if not base:
        return None
    return Path(base) / "MDS" / "workflows"


_passed = 0
_failed: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed.append(label)
        print(f"  ✗ {label}{'  ' + detail if detail else ''}")


def _req(url: str, payload: dict | None = None, timeout: float = 60.0,
         method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), _headers(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), _headers(e.headers)


def _headers(msg) -> dict:
    """HTTP 头名大小写不敏感，统一转小写再查。"""
    return {k.lower(): v for k, v in msg.items()}


def _json(url: str, payload: dict | None = None, method: str | None = None):
    status, body, _ = _req(url, payload, method=method)
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, {"_raw": body[:200].decode("utf-8", "replace")}


def wait_ready(proc: subprocess.Popen, timeout: float = 90.0) -> str:
    """读 stdout 等 MDS_READY，与 Tauri 壳的做法一致。"""
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            raise SystemExit(f"服务端启动后立即退出（退出码 {proc.returncode}）")
        line = proc.stdout.readline()
        if not line:
            continue
        line = line.strip()
        if line.startswith("MDS_READY "):
            return line.split(" ", 1)[1]
    raise SystemExit("服务端在超时内没有就绪")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", default=str(DEFAULT_EXE))
    args = p.parse_args()

    exe = Path(args.exe)
    if not exe.exists():
        raise SystemExit(f"找不到打包产物：{exe}\n先跑：pyinstaller packaging/mds-server.spec")

    print(f"启动 {exe.name} …")
    proc = subprocess.Popen(
        [str(exe), "--port", "0", "--no-open"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1)

    try:
        base = wait_ready(proc)
        print(f"已就绪：{base}\n")

        # --- 引擎数据是否随包到位 ---
        print("引擎与数据")
        _, health = _json(f"{base}/api/health")
        check(health.get("packaged") is True, "识别为打包态")
        check(health.get("workflows", 0) >= 1, "找得到可执行工作流",
              f"workflows={health.get('workflows')}（数据根解析错了？）")
        check(health.get("materials", 0) >= 10, "物料目录完整",
              f"materials={health.get('materials')}")
        check(health.get("knowledge_writable") is False,
              "随包数据标记为只读")

        # --- 完整选型 ---
        print("\n选型 0~6 阶段")
        status, body = _json(f"{base}/api/selection/run",
                             {"material": "synchronous_belt", "values": GOLDEN})
        trace = body.get("trace", {})
        check(status == 200 and trace.get("status") == "ok", "黄金用例跑通",
              str(body)[:160])
        check(trace.get("outputs", {}).get("bs") == 76.2, "结果与源码态一致（bs=76.2）",
              str(trace.get("outputs", {}).get("bs")))
        checks = trace.get("checks", [])
        check(len(checks) == 6 and all(c["detail"]["passed"] for c in checks),
              "6 项校核全通过")
        check(len(trace.get("sources", [])) == 6, "信源清单完整")
        # 采购链接走 scripts/procure_link.py，最容易在打包时掉
        check(bool((body.get("procure") or {}).get("links")), "采购链接可生成",
              str(body.get("procure"))[:120])

        # --- 拒绝外推 ---
        print("\n核心约束")
        _, body = _json(f"{base}/api/selection/run", {
            "material": "synchronous_belt", "save": False,
            "values": {**GOLDEN, "n1": 4000}})
        check(body.get("trace", {}).get("status") == "data_missing",
              "超出数据覆盖范围时拒绝外推")
        _, body = _json(f"{base}/api/selection/run", {
            "material": "synchronous_belt", "save": False,
            "values": {**GOLDEN, "belt_type": "L"}})
        check(body.get("trace", {}).get("status") == "no_solution",
              "系列内无解与数据缺失区分开")

        # --- 导出（reportlab 字体、PIL 依赖都在这条路上）---
        print("\n导出")
        status, data, headers = _req(f"{base}/api/export/pdf", {
            "material": "synchronous_belt", "values": GOLDEN})
        check(status == 200 and data.startswith(b"%PDF"), "PDF 导出",
              data[:120].decode("utf-8", "replace"))
        check("filename*=UTF-8''" in headers.get("content-disposition", ""),
              "中文文件名走 RFC 5987")
        status, data, _ = _req(f"{base}/api/export/xlsx", {
            "material": "synchronous_belt", "values": GOLDEN})
        check(status == 200 and data.startswith(b"PK"), "Excel 导出")

        # --- 只读保护 ---
        print("\n打包版的只读保护")
        status, body = _json(
            f"{base}/api/knowledge/synchronous_belt/tables/belt_pitch/verify",
            {"second_source": "冒烟测试"})
        check(status == 409, "知识库写入被明确拒绝（而不是静默失败）", str(status))

        # --- 用户自建物料：打包态最容易出问题的一处 ---
        # 随包知识库是只读的（上一条刚验过），但**用户目录必须是可写的**，
        # 否则引导式自建的物料存不下来，"下次离线也能选"就是句空话。
        # 这两件事共用一套路径解析，源码态两者都可写，测不出这个区别。
        #
        # 这里直接往用户目录里放一份规格，而不是走引导式的接口——
        # 引导式阶段 1 要真的联网取证，打包冒烟不该依赖网络和用户的 key。
        # 要验的那个打包态风险（引擎看不看得见用户目录）这样验得更直接。
        print("\n用户自建物料（引导式保存之后的那条路）")
        user_dir = _user_workflow_dir()
        check(user_dir is not None, "算得出用户数据目录",
              "LOCALAPPDATA 不在环境里？")
        probe = user_dir / "smoke_probe_material.yaml" if user_dir else None
        if probe is not None:
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text(SMOKE_SPEC, encoding="utf-8")
        check(probe is not None and probe.exists(),
              "用户目录可写（随包只读不影响它）")

        # 刚落盘的物料要立刻可见 —— 打包态 MDS_DATA_DIR 不是环境变量，
        # 而是从 LOCALAPPDATA 算出来的，引擎读不到它的话这条就会红。
        _, body = _json(f"{base}/api/materials")
        mine = next((m for m in body if m.get("id") == "smoke_probe_material"), None)
        check(mine is not None and mine.get("status") == "ready",
              "放进用户目录后引擎立刻看得见（打包态最容易漏的一条）")
        check(bool(mine) and mine.get("provenance") == "user_guided"
              and mine.get("confidence") == "unknown",
              "标成引导自建 + 最低置信度")

        status, body = _json(f"{base}/api/selection/run", {
            "material": "smoke_probe_material", "save": False,
            "values": {"F": 100, "A": 500}})
        trace = body.get("trace", {})
        check(status == 200 and trace.get("status") == "ok",
              "自建物料能真的跑起来", str(body)[:120])
        warn = " ".join(trace.get("warnings", []))
        check("引导" in warn and "不等于经过核验" in warn,
              "结果里带着「依据由你确认、但不等于核验」的警告", warn[:120])

        # 引导式在两种账号状态下都要**如实响应**，而不是 500。
        # 这台机器绑没绑账号不该决定这条断言成立与否——冒烟测的是打包产物，
        # 不是开发机的账号状态。
        status, body = _json(f"{base}/api/guided", {"material_text": "冒烟测试"})
        if status == 409:
            check("联网" in str(body),
                  "未绑定时引导式如实关闭（409 + 说明原因）", str(body)[:100])
        else:
            ok = (status == 200 and body.get("stage") == "new"
                  and not body.get("can_run"))
            check(ok, "已绑定时引导会话开得起来，且一上来什么都还不能做",
                  f"{status} {str(body)[:100]}")
            # 冒烟不该在用户的会话列表里留东西
            if body.get("id"):
                _json(f"{base}/api/guided/{body['id']}", method="DELETE")

        # 新模块真的被打进包了：取证层与检索层的登记表读得出来
        status, body = _json(f"{base}/api/account/search")
        sites = {w["domain"]: w["tier"] for w in body.get("whitelist", [])}
        check(status == 200 and sites.get("mechtool.cn") == "trusted",
              "取证与检索模块随包可用（白名单读得出来）", str(body)[:120])

        status, _ = _json(f"{base}/api/ai/saved/smoke_probe_material", method="DELETE")
        check(status == 200, "自建物料可以删除（跑完清理干净）", str(status))

        # --- 数据自检 ---
        print("\n数据自检")
        _, body = _json(f"{base}/api/knowledge/audit?probe=true")
        totals = body.get("totals", {})
        # 不写死张数——每加一个物料就改一次数字，改着改着这条断言就只剩
        # "跟上次一样"的意思了。直接数包里随附了多少张表，两边必须对得上。
        shipped = len(list((exe.parent / "_internal" / "engine" / "knowledge"
                            / "cache").glob("*/*.yaml")))
        check(shipped > 0 and totals.get("tables") == shipped,
              f"随包 {shipped} 张数据表全部可读",
              f"接口报 {totals.get('tables')} 张")
        check(totals.get("probed", 0) > 0, "可达性探测能跑（引擎真在算）")

        # --- 分支型工作流 ---
        print()
        print("分支型工作流（润滑油）")
        _, body = _json(f"{base}/api/selection/run", {
            "material": "lubricant", "save": False,
            "values": {"target": "chain", "T_work": 55, "z1": 19,
                       "p_chain": 12.7, "n_chain": 300, "chain_method": "hand"}})
        trace = body.get("trace", {})
        check(trace.get("status") == "ok", "链条分支跑通", str(body)[:140])
        check(trace.get("outputs", {}).get("iso_vg") == [150, 220, 320],
              "决策表返回的是推荐区间而不是单值",
              str(trace.get("outputs", {}).get("iso_vg")))

        # --- AI 层 ---
        # 是否绑定账号是**跑这台机器的人的状态**，不是打包产物的性质。
        # 早先这里写死了"默认未绑定"，在任何绑过账号的机器上都会假红——
        # 假红比没有断言更糟，它会让人开始忽略这份报告。
        # 打包产物真正该验的是：凭据库随包可用，且 AI 这一层不拖累选型。
        print("\nAI 层")
        _, body = _json(f"{base}/api/account")
        bound = body.get("bound")
        check(body.get("keyring_available") is True, "系统凭据库可用（后端已随包）")
        print(f"    （本机当前{'已' if bound else '未'}绑定账号）")

        _, body = _json(f"{base}/api/ai/intent", {"text": "同步带 5.5 kW 1450 r/min"})
        if bound:
            # 已绑定时走在线。网络通不通、余额够不够，都不在打包产物的可控范围内——
            # 该验的是**它怎么失败**：给一个带类型和处置建议的错误，而不是崩掉或
            # 静默返回一个空结果。余额不足返回 402 + "请前往开放平台充值"，
            # 这就是正确行为。
            detail = body.get("detail") or {}
            check(bool(body.get("source")) or bool(detail.get("error")),
                  "意图识别接口有响应（已绑定：出结果，或给出带类型的错误）",
                  str(body)[:120])
            if detail.get("error"):
                print(f"    （在线路径返回 {detail['error']}："
                      f"{str(detail.get('message', ''))[:40]} —— 这是预期内的诚实失败）")
        else:
            check(body.get("source") == "offline"
                  and body.get("material") == "synchronous_belt",
                  "离线意图识别可用")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{'─' * 56}")
    if _failed:
        print(f"通过 {_passed} 项，失败 {len(_failed)} 项：")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"全部 {_passed} 项通过 —— 打包产物可交付。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

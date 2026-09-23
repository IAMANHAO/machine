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


def _req(url: str, payload: dict | None = None, timeout: float = 60.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), _headers(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), _headers(e.headers)


def _headers(msg) -> dict:
    """HTTP 头名大小写不敏感，统一转小写再查。"""
    return {k.lower(): v for k, v in msg.items()}


def _json(url: str, payload: dict | None = None):
    status, body, _ = _req(url, payload)
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

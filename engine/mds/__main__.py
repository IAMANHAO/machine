"""mds 的命令行入口 —— 离线跑完整个选型流程，不需要前端、不需要 AI。

    python -m mds materials
    python -m mds run synchronous_belt --set P=5.5 --set n1=1450 --set belt_type=H ...
    python -m mds validate synchronous_belt
"""

from __future__ import annotations

import argparse
import io
import json
import sys

from . import procure as _procure
from . import spec as _spec
from .errors import InputError, MDSError, SpecError
from .knowledge import Knowledge
from .runner import enum_choices, run

_STATUS_LABEL = {
    "ok": "✅ 通过",
    "check_failed": "❌ 校核不通过",
    "data_missing": "⛔ 数据缺失",
    "needs_choice": "🤔 需要决策",
    "skipped": "· 未执行",
}
_CONF_LABEL = {"verified": "🟢 已双源核验", "single_source": "🟡 单一信源", "unknown": "🔴 来源不明"}


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文和 ✅ 会炸。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8",
                                                errors="replace", line_buffering=True))


def _parse_sets(items) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--set 需要 KEY=VALUE 形式，收到 {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# --- 子命令 ---------------------------------------------------------------

def cmd_materials(args) -> int:
    know = Knowledge(args.root)
    executable = set(_spec.available(args.root))
    cached = set(know.materials())
    print("物料工作流状态：\n")
    for name in sorted(executable | cached):
        marks = []
        marks.append("可执行 YAML ✓" if name in executable else "仅有 .md 文档，尚未规格化")
        marks.append(f"缓存 {len(know.tables_of(name))} 张表" if name in cached else "无缓存")
        print(f"  {name:22s} {' | '.join(marks)}")
    return 0


def cmd_inputs(args) -> int:
    spec = _spec.load(args.material, args.root)
    know = Knowledge(args.root)
    choices = enum_choices(spec, know)
    print(f"{spec.name_zh or spec.material} · 依据 {spec.standard}\n")
    for idef in spec.inputs:
        flag = "必需" if idef.required else ("二选一" if idef.one_of else "可选")
        print(f"  [{flag}] {idef.id:14s} {idef.label()}")
        if idef.hint:
            print(f"           {idef.hint}")
        for opt in choices.get(idef.id, []):
            print(f"           - {opt['value']}：{opt['label']}")
    return 0


def cmd_validate(args) -> int:
    """只校验规格能不能加载、数据表状态如何；不跑探测（那是 audit 的事）。"""
    targets = [args.material] if args.material else _spec.available(args.root)
    if not targets:
        print("没有任何可执行工作流（workflows/*.yaml）")
        return 1
    know = Knowledge(args.root)
    bad = 0
    for name in targets:
        try:
            spec = _spec.load(name, args.root)
        except SpecError as exc:
            print(f"✗ {name}: {exc}")
            bad += 1
            continue
        print(f"✓ {name}：{len(spec.inputs)} 个输入，{len(spec.steps)} 个步骤")
        for tname in know.tables_of(name):
            # 表本身可能是坏的（YAML 语法错、frontmatter 缺失）。那是**校验要报的结论**，
            # 不是让命令崩掉的理由——抛一屏栈给用户，他还得自己去猜是哪张表。
            try:
                tbl = know.table(name, tname)
            except Exception as exc:
                print(f"    ✗ 读不出来  {tname}.yaml  —— {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0][:80]}")
                bad += 1
                continue
            print(f"    {_CONF_LABEL.get(tbl.confidence, tbl.confidence)}  {tname}.yaml"
                  f"  —— {tbl.data_source[:52]}")
            if tbl.meta.get("_todo"):
                print(f"        待办：{str(tbl.meta['_todo']).strip()[:90]}")
    return 1 if bad else 0


def cmd_audit(args) -> int:
    """数据自检：信源完整性 + 表格覆盖度 + 工况可达性。"""
    from .audit import audit_material

    targets = [args.material] if args.material else sorted(
        set(_spec.available(args.root)) | set(Knowledge(args.root).materials()))
    know = Knowledge(args.root)
    worst = 0

    for name in targets:
        rep = audit_material(name, know, probe=not args.no_probe)
        c = rep["counts"]
        print(f"\n{'═' * 74}\n{name}\n{'═' * 74}")
        if not rep["has_workflow"]:
            print("  （尚无可执行工作流，只做信源体检）")

        print(f"\n【数据表】{c['tables']} 张 · "
              f"已双源核验 {c['verified']} · 具备核验条件 {c['can_be_verified']}")
        for t in rep["tables"]:
            mark = _CONF_LABEL.get(t["confidence"], t["confidence"])
            second = f" | 第二信源：{t['second_source'][:34]}" if t["second_source"] else ""
            print(f"  {mark}  {t['file']:26s} {t['data_source'][:44]}{second}")
            for issue in t["issues"]:
                print(f"      ⚠ {issue}")

        probe = rep["probe"]
        if probe["total"]:
            print(f"\n【工况可达性】{probe['reachable']} / {probe['total']} 个代表性工况能算到底")
            for m in probe["matrix"]:
                combo = " ".join(f"{k}={v}" for k, v in m["combo"].items())
                icon = {"ok": "✅", "check_failed": "⚠", "data_missing": "⛔",
                        "needs_choice": "🤔"}.get(m["status"], "·")
                tail = f"  {m['message'][:52]}" if m.get("message") else ""
                print(f"  {icon} {combo:30s} {m['status']:14s}{tail}")

        blocking = [g for g in rep["gaps"] if g["severity"] == "blocking"]
        warning = [g for g in rep["gaps"] if g["severity"] == "warning"]
        if blocking or warning:
            print(f"\n【缺口清单】阻断 {len(blocking)} · 警告 {len(warning)}")
            for g in blocking + warning:
                icon = "⛔" if g["severity"] == "blocking" else "⚠"
                print(f"  {icon} {g['message'][:88]}")
                if g["fix_hint"]:
                    print(f"       → {g['fix_hint'][:84]}")
        worst = max(worst, 1 if blocking else 0)

    print()
    return worst


def cmd_verify(args) -> int:
    """标记某张表已双源核验。必须给出第二信源。"""
    from .editor import EditError, mark_verified

    try:
        out = mark_verified(args.material, args.table,
                            second_source=args.second_source,
                            verified_by=args.by, root=args.root)
    except EditError as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2
    print(f"✅ {args.material}/{out['file']} 已标记为 verified")
    print(f"   主信源：{out['data_source']}")
    print(f"   第二信源：{out['second_source']}")
    print(f"   核验日期：{out['last_verified']}")
    return 0


def cmd_run(args) -> int:
    spec = _spec.load(args.material, args.root)
    know = Knowledge(args.root)
    values = _parse_sets(args.sets)
    choices = _parse_sets(args.choose)

    try:
        trace = run(spec, values, know, choices)
    except InputError as exc:
        if args.format == "json":
            print(json.dumps({"status": "input_error", **exc.as_dict()},
                             ensure_ascii=False, indent=2))
        else:
            print(f"⛔ {exc}\n\n用 `python -m mds inputs {args.material}` 查看完整参数清单")
        return 2

    payload = trace.to_dict()
    if trace.status == "ok" and spec.procure:
        try:
            payload["procure"] = _procure.from_spec(spec, trace.outputs)
        except MDSError as exc:
            payload["procure_error"] = str(exc)

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        _print_text(spec, trace, payload)
    return 0 if trace.status == "ok" else 1


# --- 文本报告 -------------------------------------------------------------

def _print_text(spec, trace, payload) -> None:
    line = "─" * 74
    print(f"\n{line}\n{spec.name_zh or spec.material} 选型 · 依据 {spec.standard}\n{line}")

    print("\n【阶段 2】输入参数")
    for key, val in trace.inputs.items():
        idef = spec.input(key)
        label = idef.name_zh if idef else key
        unit = f" {idef.unit}" if idef and idef.unit else ""
        print(f"  {label:18s} {key:12s} = {val}{unit}")

    print("\n【阶段 3】分步计算")
    for st in trace.steps:
        if st["kind"] == "check" or st["status"] == "skipped":
            continue
        mark = "" if st["status"] == "ok" else f"  {_STATUS_LABEL.get(st['status'], '')}"
        print(f"\n  ▸ {st['name_zh']}{mark}")
        if st["formula"]:
            print(f"      公式：{st['formula']}")
        if st["substitution"]:
            print(f"      代入：{st['substitution']}")
        if st["value"] is not None:
            unit = f" {st['unit']}" if st["unit"] else ""
            print(f"      结果：{st['value_display'] or st['value']}{unit}")
        src = st.get("source") or {}
        if src.get("ref"):
            conf = _CONF_LABEL.get(src.get("confidence", ""), "")
            print(f"      依据：{src['ref']}  {conf}")
        if st["note"]:
            print(f"      说明：{st['note']}")
        if st.get("error"):
            err = st["error"]
            if err.get("gap"):
                print(f"      缺口：{err['gap']}")
            for cand in (err.get("candidates") or []):
                detail = cand.get("detail") or {}
                extra = "  ".join(f"{k}={v}" for k, v in detail.items())
                print(f"        · {cand['value']:5s} {cand['label']}  {extra}")

    checks = trace.checks
    skipped = [s for s in trace.steps
               if s["kind"] == "check" and s["status"] == "skipped"]
    if checks or skipped:
        passed = sum(1 for c in checks if c["detail"].get("passed"))
        tail = f"，另有 {len(skipped)} 项因流程中断未执行" if skipped else ""
        print(f"\n【阶段 4】校核  {passed} / {len(checks)} 项通过{tail}")
        for c in checks:
            ok = c["detail"].get("passed")
            print(f"  {'✅' if ok else '❌'} {c['name_zh']:16s} {c['substitution']}")
            if not ok and c["detail"].get("remedy"):
                print(f"       → {c['detail']['remedy']}")
        for c in skipped:
            print(f"  ·  {c['name_zh']:16s} 未执行")

    if trace.result:
        print("\n【阶段 5】选型结果")
        for row in trace.result:
            unit = f" {row['unit']}" if row["unit"] else ""
            print(f"  {row['label']:20s} {row['value']}{unit}")

    proc = payload.get("procure")
    if proc:
        print(f"\n【阶段 6】采购链接\n  关键词：{proc['keyword']}")
        for lk in proc["links"]:
            print(f"  {lk['name']}  {lk['url']}")

    print(f"\n【信源清单】整体置信度 {_CONF_LABEL.get(trace.confidence, trace.confidence)}")
    for s in trace.sources:
        print(f"  {_CONF_LABEL.get(s['confidence'], s['confidence'])}  "
              f"{s['table_file']:26s} {s['data_source'][:44]}")

    print(f"\n【结论】{_STATUS_LABEL.get(trace.status, trace.status)}")
    if trace.blocker:
        print(f"  {trace.blocker.get('message', '')}")
    for w in trace.warnings:
        print(f"  ⚠ {w}")
    print()


def main(argv=None) -> int:
    _force_utf8()
    p = argparse.ArgumentParser(prog="mds", description="机械设计物料选型引擎（确定性内核）")
    p.add_argument("--root", default=None, help="skill 根目录，默认为本包的上级目录")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("materials", help="列出物料与其工作流/缓存状态").set_defaults(func=cmd_materials)

    pi = sub.add_parser("inputs", help="列出某物料的输入参数清单")
    pi.add_argument("material")
    pi.set_defaults(func=cmd_inputs)

    pv = sub.add_parser("validate", help="校验工作流规格与缓存数据状态")
    pv.add_argument("material", nargs="?")
    pv.set_defaults(func=cmd_validate)

    pa = sub.add_parser("audit", help="数据自检：信源 + 覆盖度 + 工况可达性")
    pa.add_argument("material", nargs="?")
    pa.add_argument("--no-probe", action="store_true", help="跳过可达性探测（更快）")
    pa.set_defaults(func=cmd_audit)

    pver = sub.add_parser("verify", help="标记数据表已双源核验（必须给第二信源）")
    pver.add_argument("material")
    pver.add_argument("table")
    pver.add_argument("--second-source", "-2", required=True,
                      help="第二个独立信源，必须与主信源不同")
    pver.add_argument("--by", default="", help="核验人")
    pver.set_defaults(func=cmd_verify)

    pr = sub.add_parser("run", help="执行一次选型")
    pr.add_argument("material")
    pr.add_argument("--set", "-s", dest="sets", action="append", metavar="KEY=VALUE")
    pr.add_argument("--choose", "-c", action="append", metavar="STEP=VALUE",
                    help="回答某个 select 步骤的决策")
    pr.add_argument("--format", "-f", choices=["text", "json"], default="text")
    pr.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (SpecError, MDSError) as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

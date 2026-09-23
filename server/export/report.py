"""报告数据模型 —— PDF 与 Excel 共用的中间层。

这里只做**投影**，不做二次加工：每一格数字都直接取自引擎的 trace。
"不存在第二份展示用数据"这条约束对导出件同样成立——导出件是要拿去
评审、归档、甚至作为采购依据的，它比界面更不能有第二套说法。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# PDF 用的 CID 字体没有 emoji 字形，留着只会变成空白方框。
# 文字本身已经把状态说清楚了，导出件里直接去掉。
_EMOJI = re.compile(
    "[🌀-🫿←-⇿☀-➿️⬀-⯿]")


def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", str(text)).replace("（）", "").strip()

CONFIDENCE_LABEL = {
    "verified": "已双源核验",
    "single_source": "单一信源（未交叉核验）",
    "self_defined": "内部整理",
    "unknown": "来源不明",
}

STATUS_LABEL = {
    "ok": "全部通过",
    "check_failed": "校核不通过",
    "data_missing": "数据缺失，流程中断",
    "no_solution": "该规格系列内无解",
    "needs_choice": "待人工决策",
}

DISCLAIMER = (
    "本报告全部数值由确定性计算引擎生成，AI 不参与任何数值计算。"
    "每一步的公式、代入值与数据出处均列于表中，可逐行独立复核。"
)


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]
    note: str = ""
    widths: list[float] | None = None


@dataclass
class Report:
    title: str
    material: str
    standard: str
    generated_at: str
    status: str
    status_label: str
    confidence: str
    confidence_label: str
    warnings: list[str] = field(default_factory=list)
    blocker: str = ""
    tables: list[Table] = field(default_factory=list)
    procure_keyword: str = ""
    procure_links: list[tuple[str, str]] = field(default_factory=list)
    procure_note: str = ""
    disclaimer: str = DISCLAIMER

    def filename_stem(self) -> str:
        stamp = self.generated_at.replace("-", "").replace(":", "").replace(" ", "_")
        return f"{self.material}_选型报告_{stamp}"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def build(trace: dict, spec, procure: dict | None = None, know=None) -> Report:
    """把一次选型的 trace 投影成可打印的报告。

    know 给进来才能把枚举值翻成中文标签——报告里留一串 ac_motor_normal
    对着图纸评审的人是没法读的。
    """
    status = trace.get("status", "unknown")
    conf = trace.get("confidence", "unknown")
    rep = Report(
        title=f"{trace.get('name_zh') or spec.material} 选型报告",
        material=spec.material,
        standard=trace.get("standard") or spec.standard,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        status=status,
        status_label=STATUS_LABEL.get(status, status),
        confidence=conf,
        confidence_label=CONFIDENCE_LABEL.get(conf, conf),
        warnings=[strip_emoji(w) for w in (trace.get("warnings") or [])],
    )
    if trace.get("blocker"):
        rep.blocker = strip_emoji(trace["blocker"].get("message", ""))

    rep.tables.append(_inputs_table(trace, spec, know))
    rep.tables.append(_steps_table(trace))
    checks = _checks_table(trace)
    if checks:
        rep.tables.append(checks)
    if trace.get("result"):
        rep.tables.append(_result_table(trace))
    rep.tables.append(_sources_table(trace))

    if procure:
        rep.procure_keyword = procure.get("keyword", "")
        rep.procure_links = [(l["name"], l["url"]) for l in procure.get("links") or []]
        rep.procure_note = procure.get("note", "")
    return rep


def _inputs_table(trace: dict, spec, know=None) -> Table:
    labels: dict[str, dict[str, str]] = {}
    if know is not None:
        try:
            from mds.runner import enum_choices
            labels = {k: {o["value"]: o["label"] for o in opts}
                      for k, opts in enum_choices(spec, know).items()}
        except Exception:
            labels = {}

    rows = []
    for key, val in (trace.get("inputs") or {}).items():
        idef = spec.input(key)
        label = (idef.name_zh if idef else "") or key
        unit = idef.unit if idef else ""
        shown = labels.get(key, {}).get(str(val)) or _fmt(val)
        rows.append([label, key, shown, unit or "—"])
    return Table(
        title="表 0 · 输入工况参数",
        columns=["参数", "符号", "取值", "单位"],
        rows=rows,
        note="以下参数由使用者提供或确认；报告可由这组输入完整复现。",
        widths=[0.22, 0.16, 0.48, 0.14],
    )


def _steps_table(trace: dict) -> Table:
    rows, idx = [], 0
    for s in trace.get("steps") or []:
        # text 是给结果表凑标签的辅助步骤，不是推导过程；
        # not_applicable 属于别的工况分支，都不该出现在计算过程汇总里
        if s.get("kind") in ("check", "text"):
            continue
        if s.get("status") in ("skipped", "not_applicable"):
            continue
        idx += 1
        src = s.get("source") or {}
        basis = src.get("ref") or ""
        if src.get("table_file"):
            basis = f"{basis}\n[{src['table_file']}]" if basis else f"[{src['table_file']}]"
        if s.get("status") != "ok":
            basis = (basis + "\n" if basis else "") + f"※ {STATUS_LABEL.get(s['status'], s['status'])}"
        rows.append([
            str(idx),
            s.get("name_zh") or s.get("id"),
            s.get("id"),
            s.get("formula") or "—",
            s.get("substitution") or "—",
            f"{s.get('value_display') or '—'} {s.get('unit') or ''}".strip(),
            basis or "—",
        ])
    return Table(
        title="表 1 · 计算过程汇总",
        columns=["序号", "项目", "符号", "公式", "代入", "结果", "依据"],
        rows=rows,
        note="每一步的公式、代入值与数据出处均已列出，可独立复核。",
        widths=[0.05, 0.15, 0.08, 0.21, 0.2, 0.12, 0.19],
    )


def _checks_table(trace: dict) -> Table | None:
    checks = trace.get("checks") or []
    skipped = [s for s in (trace.get("steps") or [])
               if s.get("kind") == "check" and s.get("status") == "skipped"]
    if not checks and not skipped:
        return None

    rows = []
    for c in checks:
        d = c.get("detail") or {}
        passed = bool(d.get("passed"))
        rows.append([
            c.get("name_zh") or c.get("id"),
            f"{c.get('substitution') or ''} {d.get('unit') or ''}".strip(),
            "通过" if passed else "不通过",
            strip_emoji((c.get("source") or {}).get("ref", "")) or "—",
            "" if passed else (d.get("remedy") or ""),
        ])
    for s in skipped:
        rows.append([s.get("name_zh") or s.get("id"), "—", "未执行", "—",
                     "流程在此之前中断，本项未校核"])

    passed_n = sum(1 for c in checks if (c.get("detail") or {}).get("passed"))
    note = f"共 {len(checks)} 项校核，通过 {passed_n} 项。"
    if skipped:
        note += f"另有 {len(skipped)} 项因流程中断未执行——未执行不等于通过。"
    return Table(
        title="表 2 · 校核",
        columns=["校核项", "判据", "结论", "依据", "不通过时的调整建议"],
        rows=rows, note=note,
        widths=[0.2, 0.22, 0.1, 0.24, 0.24],
    )


def _result_table(trace: dict) -> Table:
    rows = [[r["label"], f"{r['value']} {r.get('unit') or ''}".strip()]
            for r in trace.get("result") or []]
    return Table(
        title="表 3 · 最终选型结果",
        columns=["项目", "规格"],
        rows=rows,
        widths=[0.32, 0.68],
    )


def _sources_table(trace: dict) -> Table:
    rows = []
    for s in trace.get("sources") or []:
        rows.append([
            s.get("table_file", ""),
            s.get("data_source", "") or "—",
            s.get("second_source") or "（无）",
            CONFIDENCE_LABEL.get(s.get("confidence", ""), s.get("confidence", "")),
            _fmt(s.get("last_verified")),
            (s.get("fingerprint") or "")[:12] or "—",
        ])
    unverified = sum(1 for s in trace.get("sources") or []
                     if s.get("confidence") != "verified")
    note = ("全部数据表均已完成双源交叉核验。" if unverified == 0 else
            f"其中 {unverified} 张尚未完成双源交叉核验，结果仅供初步设计参考，"
            "正式投产前请对照标准原件复核。")
    return Table(
        title="表 4 · 信源清单",
        columns=["数据表", "主信源", "第二信源", "核验状态", "核验日期", "内容指纹"],
        rows=rows,
        note=note + " 内容指纹用于判断本报告与当前知识库是否仍然一致。",
        widths=[0.17, 0.26, 0.21, 0.15, 0.11, 0.1],
    )

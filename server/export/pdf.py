"""PDF 导出。

中文用 reportlab 内置的 STSong-Light CID 字体渲染——不用随包分发字体文件，
离线可用，也不涉及任何字体授权问题。
"""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table as PdfTable,
    TableStyle,
)

from .report import Report, Table

FONT = "STSong-Light"
_registered = False

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7a88")
LINE = colors.HexColor("#d4dce3")
HEAD_BG = colors.HexColor("#eef3f7")
OK = colors.HexColor("#0f7a4f")
WARN = colors.HexColor("#a15c00")
ERR = colors.HexColor("#b4232a")


def _ensure_font() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        _registered = True


def _styles() -> dict:
    base = getSampleStyleSheet()
    mk = lambda name, **kw: ParagraphStyle(name, parent=base["Normal"], fontName=FONT, **kw)
    return {
        "title": mk("t", fontSize=16, leading=21, textColor=INK, spaceAfter=2),
        "sub": mk("s", fontSize=9, leading=13, textColor=MUTED),
        "h2": mk("h2", fontSize=11, leading=15, textColor=INK, spaceBefore=10, spaceAfter=4),
        "body": mk("b", fontSize=8.5, leading=12, textColor=INK),
        "note": mk("n", fontSize=7.5, leading=10.5, textColor=MUTED),
        "cell": mk("c", fontSize=7.5, leading=10),
        "head": mk("hd", fontSize=7.5, leading=10, textColor=INK),
    }


def _tone(rep: Report):
    return {"ok": OK, "check_failed": ERR, "data_missing": ERR,
            "no_solution": WARN, "needs_choice": WARN}.get(rep.status, MUTED)


def render(rep: Report) -> bytes:
    """横向 A4：表 1 有七列，竖版排不下就只能挤成一团。"""
    _ensure_font()
    st = _styles()
    buf = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(
        buf, pagesize=page,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=13 * mm, bottomMargin=15 * mm,
        title=rep.title, author="机械设计物料选型引擎",
    )
    avail = page[0] - doc.leftMargin - doc.rightMargin
    flow: list = []

    # 抬头
    flow.append(Paragraph(rep.title, st["title"]))
    flow.append(Paragraph(
        f"依据 {rep.standard}　·　生成于 {rep.generated_at}　·　物料标识 {rep.material}",
        st["sub"]))
    flow.append(Spacer(1, 6))

    # 结论条
    flow.append(_summary_bar(rep, st, avail))
    flow.append(Spacer(1, 4))

    if rep.blocker:
        flow.append(_callout("流程未走完", rep.blocker, ERR, st, avail))
    for w in rep.warnings:
        flow.append(_callout("风险提示", w, WARN, st, avail))

    # 表格
    for tbl in rep.tables:
        flow.append(Paragraph(tbl.title, st["h2"]))
        flow.append(_table(tbl, st, avail))
        if tbl.note:
            flow.append(Spacer(1, 2))
            flow.append(Paragraph(tbl.note, st["note"]))
        flow.append(Spacer(1, 4))

    # 采购
    if rep.procure_keyword:
        flow.append(Paragraph("采购信息", st["h2"]))
        flow.append(Paragraph(f"采购关键词：<b>{_esc(rep.procure_keyword)}</b>", st["body"]))
        for name, url in rep.procure_links:
            flow.append(Paragraph(
                f'{_esc(name)}：<link href="{_esc(url)}" color="#1c5fa8">{_esc(url)}</link>',
                st["note"]))
        if rep.procure_note:
            flow.append(Spacer(1, 2))
            flow.append(Paragraph(rep.procure_note, st["note"]))

    doc.build(flow, onFirstPage=lambda c, d: _footer(c, d, rep),
              onLaterPages=lambda c, d: _footer(c, d, rep))
    return buf.getvalue()


def _summary_bar(rep: Report, st: dict, avail: float) -> PdfTable:
    tone = _tone(rep)
    cells = [[
        Paragraph(f"<b>结论</b><br/><font color='#{tone.hexval()[2:]}'>{rep.status_label}</font>", st["cell"]),
        Paragraph(f"<b>数据核验状态</b><br/>{rep.confidence_label}", st["cell"]),
        Paragraph(f"<b>说明</b><br/>{rep.disclaimer}", st["cell"]),
    ]]
    t = PdfTable(cells, colWidths=[avail * 0.16, avail * 0.22, avail * 0.62])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _callout(title: str, text: str, color, st: dict, avail: float) -> KeepTogether:
    t = PdfTable([[Paragraph(f"<b>{_esc(title)}</b>　{_esc(text)}", st["cell"])]],
                 colWidths=[avail])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, color),
        ("TEXTCOLOR", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return KeepTogether([t, Spacer(1, 4)])


def _table(tbl: Table, st: dict, avail: float) -> PdfTable:
    widths = tbl.widths or [1 / len(tbl.columns)] * len(tbl.columns)
    col_widths = [avail * w for w in widths]

    head = [Paragraph(f"<b>{_esc(c)}</b>", st["head"]) for c in tbl.columns]
    body = [[Paragraph(_esc(str(c)).replace("\n", "<br/>"), st["cell"]) for c in row]
            for row in tbl.rows] or [[Paragraph("（无）", st["cell"])] * len(tbl.columns)]

    t = PdfTable([head] + body, colWidths=col_widths, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    # 校核表：不通过与未执行标红，一眼能看见
    if "结论" in tbl.columns:
        col = tbl.columns.index("结论")
        for i, row in enumerate(tbl.rows, start=1):
            if row[col] != "通过":
                style.append(("TEXTCOLOR", (col, i), (col, i), ERR))
    t.setStyle(TableStyle(style))
    return t


def _footer(canvas, doc, rep: Report) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 7)
    canvas.setFillColor(MUTED)
    w, _ = doc.pagesize
    canvas.drawString(doc.leftMargin, 9 * mm,
                      f"{rep.title}　·　{rep.standard}　·　{rep.generated_at}")
    canvas.drawRightString(w - doc.rightMargin, 9 * mm, f"第 {doc.page} 页")
    canvas.setStrokeColor(LINE)
    canvas.line(doc.leftMargin, 12 * mm, w - doc.rightMargin, 12 * mm)
    canvas.restoreState()


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

"""选型报告导出 —— PDF 与 Excel 共用同一份报告模型。

导出件是要拿去评审、归档、甚至当采购依据的，所以它比界面更不能有第二套说法：
每一格都直接投影自引擎的 trace，包括信源清单与未核验提示。
"""

from .report import Report, build

__all__ = ["Report", "build", "render_pdf", "render_xlsx", "FORMATS"]

FORMATS = {
    "pdf": ("application/pdf", ".pdf"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
}


def render_pdf(report: Report) -> bytes:
    from . import pdf
    return pdf.render(report)


def render_xlsx(report: Report) -> bytes:
    from . import xlsx
    return xlsx.render(report)

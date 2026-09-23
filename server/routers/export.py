"""导出 —— 服务端重跑引擎后生成报告。

刻意**不接收前端传来的 trace**：那样等于让客户端决定报告内容。
导出接口收的是输入参数，自己跑一遍引擎再渲染——这保证了
"报告可由它自己列出的那组输入完整复现"这句话是真的。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import engine, export

router = APIRouter(tags=["export"], prefix="/export")


class ExportIn(BaseModel):
    material: str
    values: dict = Field(default_factory=dict)
    choices: dict = Field(default_factory=dict)


@router.post("/{fmt}")
def export_report(fmt: str, body: ExportIn) -> Response:
    if fmt not in export.FORMATS:
        raise HTTPException(status_code=404, detail={
            "error": "UnknownFormat",
            "message": f"不支持的导出格式 {fmt!r}，可用：{', '.join(export.FORMATS)}"})

    try:
        payload = engine.execute(body.material, body.values, body.choices)
        spec = engine.workflow_spec(body.material)
    except engine.InputError as exc:
        raise HTTPException(status_code=422, detail={
            "error": "InputError", "message": str(exc), **exc.as_dict()}) from exc
    except engine.MDSError as exc:
        raise HTTPException(status_code=400, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc

    report = export.build(payload["trace"], spec, payload["procure"],
                          know=engine.knowledge())
    data = export.render_pdf(report) if fmt == "pdf" else export.render_xlsx(report)

    media, ext = export.FORMATS[fmt]
    name = report.filename_stem() + ext
    # 中文文件名必须走 RFC 5987，否则各浏览器下载下来是乱码
    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": disposition,
                             "X-Report-Status": report.status,
                             "X-Report-Confidence": report.confidence})

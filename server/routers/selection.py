"""选型执行 —— 阶段 2~6 的后端入口。

/api/selection/run 是纯函数：相同输入 + 相同缓存版本 → 相同 trace。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import engine, store
from ..schemas import ProcureIn, RunIn, RunOut

router = APIRouter(tags=["selection"])

_STATUS_TITLE = {
    "ok": "已出结果",
    "check_failed": "校核不通过",
    "data_missing": "数据缺失",
    "needs_choice": "待决策",
}


@router.post("/selection/run", response_model=RunOut)
def run(body: RunIn) -> RunOut:
    try:
        payload = engine.execute(body.material, body.values, body.choices)
    except engine.InputError as exc:
        # 参数不合法是用户可修的问题，给结构化信息让前端定位到具体字段
        raise HTTPException(status_code=422, detail={
            "error": "InputError", "message": str(exc), **exc.as_dict()}) from exc
    except engine.SpecError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "SpecError", "message": str(exc)}) from exc
    except engine.MDSError as exc:
        raise HTTPException(status_code=400, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc

    trace = engine.note_ai_origins(payload["trace"], body.ai_filled, body.ai_aligned)
    project_id = body.project_id

    if body.save:
        summary, headline = engine.summarize(body.material, body.values, trace)
        title = _title(body, trace)
        saved = store.save_project(
            project_id=project_id,
            material=body.material,
            title=title,
            status=trace["status"],
            summary=summary,
            headline=headline,
            confidence=trace.get("confidence", "unknown"),
            values=body.values,
            choices=body.choices,
            trace=trace,
        )
        project_id = saved["id"]

    return RunOut(trace=trace, procure=payload["procure"],
                  procure_error=payload["procure_error"], project_id=project_id)


def _title(body: RunIn, trace: dict) -> str:
    if body.project_id:
        existing = store.get_project(body.project_id)
        if existing:
            return existing["title"]
    name = trace.get("name_zh") or body.material
    return f"{name}选型 #{store.next_sequence(body.material):03d}"


@router.post("/procure/links")
def procure_links(body: ProcureIn) -> dict:
    try:
        return engine.procure_build(body.material_template, body.fields,
                                    body.channels, body.extra)
    except engine.MDSError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "ProcureError", "message": str(exc)}) from exc

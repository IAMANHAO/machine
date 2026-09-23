"""物料目录与工作流定义 —— 驱动首页入口卡片与阶段 2 表单。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import engine
from ..schemas import MaterialOut, WorkflowOut

router = APIRouter(tags=["catalog"])


@router.get("/materials", response_model=list[MaterialOut])
def list_materials() -> list[dict]:
    return engine.materials()


@router.get("/workflow/{material}", response_model=WorkflowOut)
def get_workflow(material: str) -> dict:
    try:
        return engine.workflow_payload(material)
    except engine.SpecError as exc:
        raise HTTPException(status_code=404, detail={
            "error": "SpecError", "message": str(exc)}) from exc
    except engine.MDSError as exc:
        raise HTTPException(status_code=400, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc

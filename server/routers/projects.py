"""项目库 —— 首页"最近项目"。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import engine, store
from ..schemas import ProjectOut

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(limit: int = 20) -> list[dict]:
    return store.list_projects(limit)


@router.get("/projects/{pid}", response_model=ProjectOut)
def get_project(pid: str) -> dict:
    row = store.get_project(pid)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "NotFound",
                                                     "message": f"没有项目 {pid}"})
    if row.get("trace"):
        row["procure"] = engine.procure_for_trace(row["material"], row["trace"])
        # 存档之后数据表若被改过，这份结果就不再代表当前知识库
        row["stale_sources"] = engine.stale_sources(row["trace"])
    return row


@router.delete("/projects/{pid}")
def delete_project(pid: str) -> dict:
    if not store.delete_project(pid):
        raise HTTPException(status_code=404, detail={"error": "NotFound",
                                                     "message": f"没有项目 {pid}"})
    return {"deleted": pid}

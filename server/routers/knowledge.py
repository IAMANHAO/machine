"""知识库 —— 缺口自检、数据录入、双源核验。

写入一律走 mds.editor（ruamel round-trip，保留文件里的注释），
写完清引擎缓存，保证界面与下一次计算看到的是同一份数据。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import engine
from ..config import knowledge_writable

router = APIRouter(tags=["knowledge"], prefix="/knowledge")


# --- 入参 ------------------------------------------------------------------

class VerifyIn(BaseModel):
    second_source: str = Field(description="第二个独立信源，必须与主信源不同")
    verified_by: str = ""


class UnverifyIn(BaseModel):
    reason: str = ""


class SourcesIn(BaseModel):
    data_source: str | None = None
    second_source: str | None = None
    note: str | None = None


class PointsIn(BaseModel):
    group: str = Field(description="分组路径，如 belt_type_H")
    rows_key: str = "examples"
    x_field: str
    x_value: float
    y_field: str
    z_field: str
    points: list[dict[str, float]]


class ScalarIn(BaseModel):
    path: str = Field(description="正文里的点分路径，如 belt_types.H.vmax")
    value: Any


# --- 读 --------------------------------------------------------------------

@router.get("/audit")
def audit_all(probe: bool = True) -> dict:
    """所有物料的体检汇总（首页/知识库总览用）。"""
    return engine.audit_all(probe=probe)


@router.get("/{material}/audit")
def audit_one(material: str, probe: bool = True) -> dict:
    try:
        return engine.audit_material(material, probe=probe)
    except engine.MDSError as exc:
        raise HTTPException(status_code=404, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc


@router.get("/{material}/tables/{name}")
def read_table(material: str, name: str) -> dict:
    try:
        return engine.read_table(material, name)
    except engine.MDSError as exc:
        raise HTTPException(status_code=404, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc


# --- 写 --------------------------------------------------------------------

def _edit(fn, *args, **kwargs) -> dict:
    """统一处理写入异常，并在成功后清引擎缓存。"""
    from mds.editor import EditError

    if not knowledge_writable():
        raise HTTPException(status_code=409, detail={
            "error": "ReadOnlyKnowledge",
            "message": "当前知识库是只读的（安装版随包分发的数据不可修改）。"
                       "要补录数据或标记核验，请用源码方式运行，"
                       "或设置 MDS_SKILL_ROOT 指向一份可写的数据目录。"})

    try:
        out = fn(*args, **kwargs)
    except EditError as exc:
        raise HTTPException(status_code=422, detail={
            "error": "EditError", "message": str(exc)}) from exc
    except engine.MDSError as exc:
        raise HTTPException(status_code=400, detail={
            "error": type(exc).__name__, "message": str(exc)}) from exc
    engine.reset_caches()
    return out


@router.post("/{material}/tables/{name}/verify")
def verify(material: str, name: str, body: VerifyIn) -> dict:
    """标为已双源核验。没有第二信源会被 422 拒绝——假绿灯比没有绿灯更危险。"""
    from mds import editor
    return _edit(editor.mark_verified, material, name,
                 second_source=body.second_source, verified_by=body.verified_by)


@router.post("/{material}/tables/{name}/unverify")
def unverify(material: str, name: str, body: UnverifyIn) -> dict:
    from mds import editor
    return _edit(editor.unverify, material, name, body.reason)


@router.patch("/{material}/tables/{name}/sources")
def set_sources(material: str, name: str, body: SourcesIn) -> dict:
    from mds import editor
    return _edit(editor.set_sources, material, name,
                 data_source=body.data_source, second_source=body.second_source,
                 note=body.note)


@router.post("/{material}/tables/{name}/points")
def upsert_points(material: str, name: str, body: PointsIn) -> dict:
    from mds import editor
    return _edit(editor.upsert_points, material, name,
                 group=body.group, rows_key=body.rows_key,
                 x_field=body.x_field, x_value=body.x_value,
                 y_field=body.y_field, z_field=body.z_field, points=body.points)


@router.patch("/{material}/tables/{name}/scalar")
def set_scalar(material: str, name: str, body: ScalarIn) -> dict:
    from mds import editor
    return _edit(editor.set_scalar, material, name, path=body.path, value=body.value)

"""项目持久化 —— SQLite

对应原型图首页的"最近项目"。存的是输入 + 决策 + 最后一次 trace，
重新打开一个项目时可以直接重算（trace 是纯函数结果，不必信任存档里的数字）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    material    TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    headline    TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'unknown',
    values_json TEXT NOT NULL DEFAULT '{}',
    choices_json TEXT NOT NULL DEFAULT '{}',
    trace_json  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row, *, with_trace: bool = False) -> dict:
    out = {
        "id": row["id"],
        "material": row["material"],
        "title": row["title"],
        "status": row["status"],
        "summary": row["summary"],
        "headline": row["headline"],
        "confidence": row["confidence"],
        "values": json.loads(row["values_json"] or "{}"),
        "choices": json.loads(row["choices_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_trace and row["trace_json"]:
        out["trace"] = json.loads(row["trace_json"])
    return out


def list_projects(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_project(pid: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return _row_to_dict(row, with_trace=True) if row else None


def save_project(*, project_id: str | None, material: str, title: str, status: str,
                 summary: str = "", headline: str = "", confidence: str = "unknown",
                 values: dict[str, Any] | None = None,
                 choices: dict[str, Any] | None = None,
                 trace: dict | None = None) -> dict:
    pid = project_id or uuid.uuid4().hex[:12]
    now = _now()
    payload = (
        material, title, status, summary, headline, confidence,
        json.dumps(values or {}, ensure_ascii=False),
        json.dumps(choices or {}, ensure_ascii=False),
        json.dumps(trace, ensure_ascii=False, default=str) if trace else None,
        now,
    )
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone()
        if exists:
            conn.execute(
                "UPDATE projects SET material=?, title=?, status=?, summary=?, headline=?,"
                " confidence=?, values_json=?, choices_json=?, trace_json=?, updated_at=?"
                " WHERE id=?", (*payload, pid))
        else:
            conn.execute(
                "INSERT INTO projects (material, title, status, summary, headline,"
                " confidence, values_json, choices_json, trace_json, updated_at,"
                " id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*payload, pid, now))
    return get_project(pid)  # type: ignore[return-value]


def delete_project(pid: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    return cur.rowcount > 0


def next_sequence(material: str) -> int:
    """给项目起名用的流水号：同步带选型 #024。"""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
    return int(row["n"]) + 1

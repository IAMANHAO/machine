"""mds.knowledge —— 缓存数据表的加载与信源元数据

缓存 YAML 是"frontmatter 文档 + 正文文档"两段式：

    ---
    data_source: GB/T 11362-2021
    verification_status: single_source
    ---

    belt_types:
      ...

PyYAML 的 safe_load 遇到多文档会抛 ComposerError，必须用 safe_load_all 合并。
（原 scripts/_yaml.py 走的是 safe_load，在装有 PyYAML 的环境下读不了任何一张缓存表。）
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import yaml

from .errors import DataMissing

def _default_root() -> Path:
    """skill 根，同时也是引擎的数据根。

    打包后（PyInstaller）mds 是从 PYZ 归档里加载的，__file__ 指向的不是数据目录，
    所以必须允许用 MDS_SKILL_ROOT 指定。源码运行时这个变量通常不设，按相对路径推算。
    """
    raw = os.environ.get("MDS_SKILL_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


# engine/ —— skill 根，同时也是引擎的数据根
SKILL_ROOT = _default_root()

# frontmatter 里属于"元数据"的键，不参与正文合并
# second_source / verified_by 是 M3 引入的：标 verified 必须能追溯到第二个独立信源
META_KEYS = {"data_source", "second_source", "last_verified", "verified_by",
             "verification_status", "unit_notes", "_todo"}

# 置信度分级：对应 UI 的 🟢 / 🟡 / 🔴
CONFIDENCE_ORDER = {"verified": 2, "single_source": 1, "unknown": 0}


class Table:
    """一张缓存数据表：正文 + 信源元数据。"""

    __slots__ = ("material", "name", "path", "data", "meta", "fingerprint")

    def __init__(self, material: str, name: str, path: Path, data: dict, meta: dict,
                 fingerprint: str = ""):
        self.material = material
        self.name = name
        self.path = path
        self.data = data
        self.meta = meta
        # 内容指纹：项目存档后数据表若被改过，界面要能提示"建议重算"
        self.fingerprint = fingerprint

    @property
    def confidence(self) -> str:
        return self.meta.get("verification_status") or "unknown"

    @property
    def data_source(self) -> str:
        return self.meta.get("data_source") or ""

    def source_info(self) -> dict:
        return {
            "table_file": f"{self.name}.yaml",
            "data_source": self.data_source,
            "second_source": self.meta.get("second_source") or "",
            "confidence": self.confidence,
            "last_verified": self.meta.get("last_verified"),
            "fingerprint": self.fingerprint,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Table {self.material}/{self.name} {self.confidence}>"


def _read_yaml_docs(path: Path) -> tuple[dict, dict]:
    """读取两段式 YAML，返回 (正文, 元数据)。"""
    text = path.read_text(encoding="utf-8")
    body: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    for doc in yaml.safe_load_all(text):
        if not isinstance(doc, dict):
            continue
        for key, value in doc.items():
            if key in META_KEYS:
                meta[key] = value
            else:
                body[key] = value
    return body, meta


class Knowledge:
    """缓存目录的只读访问入口，带进程内缓存。"""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else SKILL_ROOT
        self.cache_root = self.root / "knowledge" / "cache"
        self._tables: dict[tuple[str, str], Table] = {}
        self._index: dict | None = None

    # --- 数据表 ---
    def table(self, material: str, name: str) -> Table:
        name = name[:-5] if name.endswith(".yaml") else name
        key = (material, name)
        if key in self._tables:
            return self._tables[key]

        path = self.cache_root / material / f"{name}.yaml"
        if not path.exists():
            available = sorted(p.stem for p in (self.cache_root / material).glob("*.yaml")) \
                if (self.cache_root / material).is_dir() else []
            raise DataMissing(
                f"缓存中没有数据表 {material}/{name}.yaml",
                table=f"{material}/{name}", available=available,
                gap=f"需要新建 knowledge/cache/{material}/{name}.yaml",
            )
        body, meta = _read_yaml_docs(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        tbl = Table(material, name, path, body, meta, digest)
        self._tables[key] = tbl
        return tbl

    def tables_of(self, material: str) -> list[str]:
        d = self.cache_root / material
        return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []

    def materials(self) -> list[str]:
        if not self.cache_root.is_dir():
            return []
        return sorted(p.name for p in self.cache_root.iterdir() if p.is_dir())

    # --- 索引 ---
    def index(self) -> dict:
        if self._index is None:
            path = self.root / "knowledge" / "index.yaml"
            self._index = _read_yaml_docs(path)[0] if path.exists() else {}
        return self._index


def lowest_confidence(levels) -> str:
    """一次选型的整体置信度 = 所有被引用数据表里最低的那一档。"""
    levels = [lv for lv in levels if lv]
    if not levels:
        return "unknown"
    return min(levels, key=lambda lv: CONFIDENCE_ORDER.get(lv, 0))

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


def user_root() -> Path | None:
    """用户自己的工作流与数据目录 —— 可写，放 AI 起草并保存下来的物料。

    随包数据是只读的（装在 Program Files 里本来也写不进去），
    所以用户新增的物料必须落在另一处。没设就返回 None，引擎照常只用内置的。

    **每次都读环境变量，不缓存成模块级常量。** SKILL_ROOT 那样定死是因为它在
    打包态由启动器提前设好；而数据目录可能在进程跑起来之后才确定（测试、
    多实例、用户在设置页改路径），定死会让后设的值永远读不到。
    """
    raw = os.environ.get("MDS_USER_ROOT") or os.environ.get("MDS_DATA_DIR")
    return Path(raw).expanduser().resolve() if raw else None

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

    def __init__(self, root: Path | str | None = None,
                 user_root: Path | str | None = None):
        self.root = Path(root) if root else SKILL_ROOT
        self.cache_root = self.root / "knowledge" / "cache"
        # 用户目录里的表。**只补充，不覆盖**：同名物料以内置为准。
        ur = Path(user_root) if user_root else _user_root()
        self.user_root = ur
        self.user_cache_root = (ur / "knowledge" / "cache") if ur else None
        self._tables: dict[tuple[str, str], Table] = {}
        self._index: dict | None = None

    def _cache_dirs(self) -> list[Path]:
        """查表顺序：内置优先，用户目录兜底。"""
        dirs = [self.cache_root]
        if self.user_cache_root:
            dirs.append(self.user_cache_root)
        return dirs

    # --- 数据表 ---
    def table(self, material: str, name: str) -> Table:
        name = name[:-5] if name.endswith(".yaml") else name
        key = (material, name)
        if key in self._tables:
            return self._tables[key]

        path = next((d / material / f"{name}.yaml" for d in self._cache_dirs()
                     if (d / material / f"{name}.yaml").exists()),
                    self.cache_root / material / f"{name}.yaml")
        if not path.exists():
            available = sorted({p.stem for d in self._cache_dirs()
                                if (d / material).is_dir()
                                for p in (d / material).glob("*.yaml")})
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
        return sorted({p.stem for d in self._cache_dirs()
                       if (d / material).is_dir()
                       for p in (d / material).glob("*.yaml")})

    def materials(self) -> list[str]:
        out: set[str] = set()
        for d in self._cache_dirs():
            if d.is_dir():
                out.update(p.name for p in d.iterdir() if p.is_dir())
        return sorted(out)

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


# 形参 user_root 会遮住同名函数，内部统一走这个别名
_user_root = user_root

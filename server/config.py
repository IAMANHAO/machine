"""服务端配置 —— 路径解析与运行时设置。

同一份代码要在两种形态下工作：

- **开发态**：从源码树跑，引擎数据在 skill 目录里，用户数据放仓库根的 .mds-data/
- **打包态**（PyInstaller / Tauri）：引擎数据随包只读分发，用户数据必须写到
  用户目录（%LOCALAPPDATA%），因为 Program Files 是只读的——
  写在 exe 旁边会让项目保存静默失败。

两种形态都可以用环境变量覆盖：MDS_SKILL_ROOT / MDS_DATA_DIR。
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "MDS"

# PyInstaller 把数据解包到 sys._MEIPASS；非打包态该属性不存在
FROZEN = getattr(sys, "frozen", False)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else None

# 进程启动时用户显式设定的值——必须在 ensure_engine_importable() 回写之前抓下来，
# 否则我们自己写进去的值会被误当成"用户指定了外部数据目录"。
USER_SKILL_ROOT = os.environ.get("MDS_SKILL_ROOT")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILL_ROOT = REPO_ROOT / "engine"


def _bundled(name: str) -> Path | None:
    if BUNDLE_DIR is None:
        return None
    path = BUNDLE_DIR / name
    return path if path.exists() else None


# 进程启动时用户显式设的值。之后 ensure_engine_importable() 会覆写
# MDS_USER_ROOT，所以必须在那之前记下来，否则分不清是谁写的。
_EXPLICIT_USER_ROOT = os.environ.get("MDS_USER_ROOT") or ""


def skill_root() -> Path:
    """引擎数据根（workflows/ + knowledge/）。打包态下是只读的。"""
    if USER_SKILL_ROOT:
        root = Path(USER_SKILL_ROOT).expanduser().resolve()
    else:
        root = _bundled("engine") or DEFAULT_SKILL_ROOT

    if not (root / "workflows").is_dir():
        raise RuntimeError(
            f"找不到引擎数据根目录：{root}\n"
            f"请设置环境变量 MDS_SKILL_ROOT 指向 engine/")
    return root


def ensure_engine_importable() -> Path:
    """把引擎挂上 sys.path，并把解析结果回写环境变量。

    回写是必需的：mds.knowledge 在 import 时确定自己的数据根，打包后它
    没法从 __file__ 推出随包数据的位置，只能靠 MDS_SKILL_ROOT。

    **用户目录同理，而且更隐蔽**：打包态 data_dir() 是从 LOCALAPPDATA 算出来的，
    并不是环境变量。不回写的话 mds 那边 user_root() 读不到任何东西——
    结果就是草稿存得进去、引擎却找不到，物料列表里看不见。
    这个 bug 只在打包态出现（源码态 MDS_DATA_DIR 通常由测试或 run.ps1 设好），
    是 packaging/smoke_test.py 抓出来的。
    """
    root = skill_root()
    os.environ["MDS_SKILL_ROOT"] = str(root)
    # MDS_DATA_DIR 在场时**什么都不做**：mds.knowledge.user_root() 本来就会读它，
    # 而且读的是"此刻"的值。在这里回写反而会把某一刻的值钉死，
    # 之后改 MDS_DATA_DIR（测试、多实例）全部失效。
    # 只有它不在场时（打包态从 LOCALAPPDATA 算出来）才需要我们告诉引擎去哪找。
    if not os.environ.get("MDS_DATA_DIR") and not _EXPLICIT_USER_ROOT:
        os.environ["MDS_USER_ROOT"] = str(data_dir())
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def knowledge_writable() -> bool:
    """知识库能不能改。

    打包态下随包分发的数据是只读的（装在 Program Files 里根本写不进去），
    知识库页必须据此把编辑按钮禁掉，而不是让用户点了才报错。
    """
    if USER_SKILL_ROOT:
        # 用户自己指了一份数据目录，可写与否以文件系统为准
        return os.access(skill_root(), os.W_OK)
    if FROZEN:
        # 随包分发的数据视为只读，哪怕这台机器上碰巧能写
        return False
    return os.access(skill_root(), os.W_OK)


def data_dir() -> Path:
    """用户数据（项目库、账号绑定元数据、AI 用量）。必须可写。"""
    raw = os.environ.get("MDS_DATA_DIR")
    if raw:
        d = Path(raw).expanduser()
    elif FROZEN:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
        d = (Path(base) / APP_NAME) if base else (Path.home() / f".{APP_NAME.lower()}")
    else:
        d = REPO_ROOT / ".mds-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "projects.db"


@lru_cache(maxsize=1)
def web_dist() -> Path | None:
    """前端构建产物；未构建时返回 None（开发期走 Vite dev server）。"""
    bundled = _bundled("web")
    if bundled and (bundled / "index.html").exists():
        return bundled
    dist = REPO_ROOT / "web" / "dist"
    return dist if (dist / "index.html").exists() else None


def describe() -> dict:
    """给设置页/诊断用的运行环境概况。"""
    return {
        "frozen": FROZEN,
        "skill_root": str(skill_root()),
        "data_dir": str(data_dir()),
        "web_dist": str(web_dist()) if web_dist() else None,
        "knowledge_writable": knowledge_writable(),
    }


# 开发期 Vite dev server 的地址，用于 CORS 放行
DEV_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]

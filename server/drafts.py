"""引导式选型出来的物料的落盘 —— 存进用户目录，下次离线也能选。

## 为什么要单独存一处

随包的 `engine/workflows/` 是**只读**的（安装版装在 Program Files 里本来也写不进去），
而且那里放的是经过核验、有信源的工作流。用户自建的规格不该混进去。

所以另开一个用户目录（`MDS_DATA_DIR/workflows/`），引擎查找时**内置优先、
用户目录兜底**——已核验的工作流永远不会被一份 🔴 的自建规格顶掉。

## 什么时候存

**跑完一次完整选型之后**，由用户决定存不存。不在引导结束时就自动落盘：
一份没跑通的流程存下来只会在物料列表里留一个点进去就报错的入口。

## 存下来的东西带什么标记

- `provenance: user_guided` —— 界面、报告、导出件据此全程打标
- `basis` —— 用户确认的那条依据：标准号、被取证的 URL、抓取时间、内容指纹、
  印证分档。**这是它与旧版「AI 一次性起草」最大的区别**：那时候一份规格背后
  什么都没有，现在背后有一条能点开核对的依据。
- `generated_by` —— 引导它的模型，便于追溯
- 置信度按**最低档**算，与"数据表缺信源"同级

存下来的是**流程**，不是数据。闸门保证它不携带任何数据表、公式里也没有编出来的
系数，所有需要查手册的量都是用户自己填的输入项。

旧版 `ai_generated` 的文件仍然读得进来、跑得起来（只读兼容），但不再新建。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import data_dir

# 物料 id 只允许这些字符：它会变成文件名，也会进 URL
_ID_OK = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# 落盘文件顶部的说明块。拆成两段常量是为了让中间能插入这份规格自己的依据清单。
HEADER_TITLE = (
    "## 本文件由「引导式选型」生成：依据由你确认，整套公式由你过目确认\n"
    "#\n"
    "# 它能跑，是因为过了三道闸门：\n"
    "#   1. 格式与表达式合法（与随包工作流同一个解析器）\n"
    "#   2. 不引用任何数据表，公式里也没有编出来的系数\n"
    "#   3. 每个公式的 source.ref 都落在你确认的那条依据之内\n"
    "#\n")

HEADER_TAIL = (
    "#\n"
    "# 但**取证只证明那份文件里确实有这个标准号，不证明这个公式适用于你的工况**。\n"
    "# 正式设计前请对照依据原文复核。核对无误后可以手工把 provenance 改成 user。\n"
    "#\n")


class DraftError(Exception):
    """存草稿失败。message 面向用户。"""


def user_workflow_dir() -> Path:
    d = data_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def saved() -> list[str]:
    """用户目录里已保存的物料 id。"""
    return sorted(p.stem for p in user_workflow_dir().glob("*.yaml"))


def _builtin_ids() -> set[str]:
    """随包物料。用户目录不得与之重名——重名会让人以为自己改的是随包那份。"""
    from mds import spec as mds_spec
    from mds.knowledge import SKILL_ROOT
    return set(mds_spec.available(SKILL_ROOT))


def save(spec_data: dict) -> dict:
    """把一份已通过闸门的草稿写进用户目录。

    这里再做一次独立校验——不信任调用方，也不信任草稿在内存里待过一段时间。
    """
    if not isinstance(spec_data, dict):
        raise DraftError("草稿格式不对：顶层必须是一个映射。")

    mid = str(spec_data.get("material") or "").strip()
    if not _ID_OK.match(mid):
        raise DraftError(
            f"物料 id {mid!r} 不合法：只能用小写字母、数字与下划线，字母开头，2~41 字符。")
    if mid in _builtin_ids():
        raise DraftError(
            f"{mid!r} 与随包物料重名。随包物料是经过信源标注的，"
            "不能被生成的规格顶掉——请换一个 id。")

    # 落盘前再过一遍闸门：格式合法 + 不携带数据表 + 公式里没有编出来的系数。
    # **判据与引导过程中用的是同一个函数** —— 两份判据迟早会对不上。
    from mds import spec as mds_spec

    from .ai.tasks import audit_steps
    from .research import claim_keys

    basis = dict(spec_data.get("basis") or {})
    reasons = audit_steps(
        spec_data, basis_keys=claim_keys(str(basis.get("claim") or "")))
    if reasons:
        raise DraftError("这份规格没通过合规检查：" + "；".join(reasons[:3]))
    try:
        parsed = mds_spec.parse(spec_data)
    except Exception as exc:
        raise DraftError(f"这份规格没通过引擎静态校验：{exc}") from exc

    payload = dict(spec_data)
    payload["provenance"] = "user_guided"
    payload["saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    urls = [str(u) for u in (basis.get("urls") or []) if u][:4]
    path = user_workflow_dir() / f"{mid}.yaml"
    header = (
        HEADER_TITLE
        + f"# 依据：{basis.get('claim') or '未记录'}"
          f"（取证：{basis.get('status') or '未取证'}）\n"
        + "".join(f"#   {u}\n" for u in urls)
        + HEADER_TAIL
        + f"# 引导模型：{payload.get('generated_by') or '未知'}\n"
          f"# 保存时间：{payload['saved_at']}\n\n")
    path.write_text(
        header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False,
                                default_flow_style=False),
        encoding="utf-8")

    return {
        "saved": True,
        "material": mid,
        "name_zh": parsed.name_zh,
        "path": str(path),
        "provenance": "user_guided",
        "basis": basis,
        "note": ("已保存到用户目录，下次**离线也能选**这个物料。"
                 "它在界面上会标成 🔴 未经核验——那是准确的现状："
                 "依据是真的、可点开的，但没有人拿标准原件逐格核对过。"),
    }


def remove(material: str) -> dict:
    """删掉一个已保存的自建物料。随包物料删不掉（也不在这个目录里）。"""
    path = user_workflow_dir() / f"{material}.yaml"
    if not path.exists():
        raise DraftError(f"用户目录里没有 {material!r}。随包物料不能从这里删除。")
    path.unlink()
    return {"removed": True, "material": material}

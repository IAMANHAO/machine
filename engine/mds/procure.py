"""mds.procure —— 采购链接（阶段 6）

不重写 scripts/procure_link.py 的关键词拼装逻辑——那套 field_prefix / field_alias /
变体策略是踩过坑（"模数6205"）才调对的，重写一遍只会把坑再踩一次。
这里把它作为模块导入复用，只补两件事：

1. 把 sys.exit() 换成异常，让它能被当作库调用而不是只能当 CLI
2. 按工作流规格的 procure 段 + 选型结果 env，自动拼出字段
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .errors import MDSError
from .expr import fmt_num
from .knowledge import SKILL_ROOT

_TEMPLATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_module():
    """加载关键词拼装模块，并把配置路径显式钉死。

    procure_link 自己用 __file__ 推算 SKILL_ROOT。打包后它是从归档里加载的，
    推出来的路径不存在——所以这里按 mds 解析出的真实数据根覆盖掉它。
    """
    scripts = SKILL_ROOT / "scripts"
    if scripts.is_dir() and str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import procure_link  # type: ignore

    config = SKILL_ROOT / "knowledge" / "procure" / "keyword_templates.yaml"
    if config.exists():
        procure_link.SKILL_ROOT = SKILL_ROOT
        procure_link.CONFIG_PATH = config
    return procure_link


def build(material_template: str, fields: dict, channels: list[str] | None = None,
          extra: str = "") -> dict:
    """拼关键词并生成各渠道搜索链接。"""
    pl = _load_module()
    try:
        cfg = pl.load_config()
        _key, block = pl.resolve_material(cfg, material_template)
        keywords = pl.compose_keyword(block, fields, extra)
        if not keywords or not keywords[0]:
            raise MDSError("采购关键词为空：至少需要给出物料名（kind）")
        chans = pl.normalize_channels(cfg, channels, block)
        links = pl.build_links(cfg, keywords[0], chans)
    except SystemExit as exc:  # procure_link 用 sys.exit 报错，转成异常
        raise MDSError(f"采购链接生成失败：{exc}") from exc

    return {
        "material_template": material_template,
        "keyword": keywords[0],
        "keyword_variants": keywords[1:],
        "links": links,
        "note": "链接为搜索页而非具体商品，价格与库存随行情浮动，需自行核对；"
                "油品、轴承等易仿冒品类请认准品牌旗舰店或授权经销，并索取检测报告。",
    }


def from_spec(spec, env: dict) -> dict | None:
    """按工作流规格的 procure 段 + 选型结果生成链接。"""
    cfg = getattr(spec, "procure", None)
    if not cfg:
        return None

    def render(text: str) -> str:
        return _TEMPLATE.sub(
            lambda m: fmt_num(env[m.group(1)]) if m.group(1) in env else m.group(0),
            str(text))

    fields: dict[str, Any] = {k: render(v) for k, v in (cfg.get("fields") or {}).items()}
    return build(cfg.get("material_template", spec.material), fields,
                 cfg.get("channels"), cfg.get("extra", ""))


def to_markdown(result: dict) -> str:
    lines = [f"**采购关键词**：`{result['keyword']}`", "", "| 渠道 | 搜索链接 |", "|---|---|"]
    for lk in result.get("links") or []:
        lines.append(f"| {lk['name']} | [{lk['keyword']}]({lk['url']}) |")
    if result.get("keyword_variants"):
        lines += ["", "**备选关键词**：" + "、".join(f"`{v}`" for v in result["keyword_variants"])]
    lines += ["", f"> {result.get('note', '')}"]
    return "\n".join(lines)

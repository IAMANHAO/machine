#!/usr/bin/env python3
"""
procure_link.py —— 选型完成后生成采购搜索链接（淘宝 / 天猫 / 1688 / 京东）

设计目标：选型结果落到"能买"这一步。
输入选型得到的关键规格，输出可直接点击的电商搜索链接（默认淘宝）。

用法
----

# 1. 按物料模板拼关键词并生成链接（最常用）
python scripts/procure_link.py build --material lubricant \
    --set brand=美孚 --set product=威达Vactra --set vg=68 \
    --set kind=导轨油 --set pack=18L

# 2. 只要关键词，不要链接
python scripts/procure_link.py keyword --material lubricant --set kind=导轨油 --set vg=68

# 3. 已有完整关键词，直接生成链接
python scripts/procure_link.py link --keyword "美孚 威达 VG68 导轨油 18L" --channels taobao,tmall,1688

# 4. 查看支持的物料模板 / 渠道
python scripts/procure_link.py materials
python scripts/procure_link.py channels

输出为 JSON（--format md 输出可直接粘进选型结果表的 Markdown）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _yaml import load as yaml_load  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "knowledge" / "procure" / "keyword_templates.yaml"

# 渠道别名
_CHANNEL_ALIAS = {
    "tb": "taobao",
    "淘宝": "taobao",
    "tm": "tmall",
    "天猫": "tmall",
    "1688": "alibaba1688",
    "阿里": "alibaba1688",
    "阿里巴巴": "alibaba1688",
    "jd": "jd",
    "京东": "jd",
}


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"找不到采购配置：{CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml_load(f)
    if not isinstance(cfg, dict):
        sys.exit("采购配置解析失败（不是 dict）")
    return cfg


def resolve_material(cfg: dict, material: str) -> tuple[str, dict]:
    """返回 (material_key, 合并 defaults 后的物料块)。"""
    materials = cfg.get("materials") or {}
    if material in materials:
        key = material
    elif "generic" in materials:
        key = "generic"
    else:
        sys.exit(f"配置中既没有 {material} 也没有 generic 兜底模板")

    merged = dict(cfg.get("defaults") or {})
    merged.update(materials[key] or {})
    return key, merged


def available_materials(cfg: dict) -> list[str]:
    return [k for k in (cfg.get("materials") or {}).keys() if k != "generic"]


# ---------------------------------------------------------------------------
# 关键词拼装
# ---------------------------------------------------------------------------
def _render_value(field: str, value, prefixes: dict | None = None, aliases: dict | None = None) -> str:
    if aliases and field in aliases:
        field = aliases[field]
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if field == "vg":
        # 68 -> VG68；已带 VG/号 前缀的原样保留
        if re.fullmatch(r"\d+", text):
            return f"VG{text}"
    prefix = (prefixes or {}).get(field, "")
    # 仅当字段值是纯数字时才补前缀，避免把 "M2"/"1比30" 这类已带语义的写法改坏
    if prefix and re.fullmatch(r"\d+(\.\d+)?", text):
        return f"{prefix}{text}"
    return text


def compose_keyword(block: dict, fields: dict, extra: str = "", drop: list | None = None) -> list[str]:
    """按 keyword_order 拼关键词，返回 [主关键词] + 变体。"""
    order = list(block.get("keyword_order") or [])
    prefixes = block.get("field_prefix") or {}
    aliases = block.get("field_alias") or {}
    drop = set(drop or [])
    fields = _normalize_fields(fields, order, aliases)
    parts: list[str] = []
    seen = set()
    for name in order:
        if name in drop:
            continue
        rendered = _render_value(name, fields.get(name), prefixes, None)
        if rendered and rendered.lower() not in seen:
            parts.append(rendered)
            seen.add(rendered.lower())
    # 未出现在 keyword_order 里的字段，追加到末尾，避免用户传了却被丢掉
    for name, value in fields.items():
        if name in order:
            continue
        rendered = _render_value(name, value, prefixes, None)
        if rendered and rendered.lower() not in seen:
            parts.append(rendered)
            seen.add(rendered.lower())
    kw = " ".join(parts).strip()
    if extra:
        kw = f"{kw} {extra.strip()}".strip()

    variants: list[str] = []
    # 变体 1：精简（丢弃 variant_drop 中的字段）
    slim = compose_parts(order, fields, set(block.get("variant_drop") or []) | drop, extra, prefixes)
    if slim and slim != kw:
        variants.append(slim)
    # 变体 2：黏度改用"号"写法（淘宝卖家更常用的写法）
    if "vg" in fields:
        raw = str(fields["vg"]).strip()
        if raw.isdigit():
            alt = dict(fields)
            alt["vg"] = f"{raw}号"
            alt_kw = compose_parts(order, alt, drop, extra, prefixes)
            if alt_kw and alt_kw not in variants and alt_kw != kw:
                variants.append(alt_kw)
    return [kw] + [v for v in variants if v]


def _normalize_fields(fields: dict, order: list, aliases: dict) -> dict:
    """把别名键归并到规范键；规范键已显式给出时不覆盖。"""
    if not aliases:
        return dict(fields)
    out: dict = {}
    for k, v in fields.items():
        out[k] = v
    for alias, canonical in aliases.items():
        if alias in fields and alias != canonical and canonical not in fields:
            out[canonical] = fields[alias]
            out.pop(alias, None)
    return out


def compose_parts(order: list, fields: dict, drop: set, extra: str = "", prefixes: dict | None = None) -> str:
    parts, seen = [], set()
    for name in order:
        if name in drop:
            continue
        rendered = _render_value(name, fields.get(name), prefixes, None)
        if rendered and rendered.lower() not in seen:
            parts.append(rendered)
            seen.add(rendered.lower())
    for name, value in fields.items():
        if name in order or name in drop:
            continue
        rendered = _render_value(name, value, prefixes, None)
        if rendered and rendered.lower() not in seen:
            parts.append(rendered)
            seen.add(rendered.lower())
    kw = " ".join(parts).strip()
    if extra:
        kw = f"{kw} {extra.strip()}".strip()
    return kw


# ---------------------------------------------------------------------------
# 链接生成
# ---------------------------------------------------------------------------
def normalize_channels(cfg: dict, raw: list[str] | None, block: dict | None = None) -> list[str]:
    channels_cfg = cfg.get("channels") or {}
    if not raw:
        default = (block or {}).get("channels")
        if not default:
            default = [k for k, v in channels_cfg.items() if (v or {}).get("default")]
        if not default:
            default = ["taobao"]
        return [c for c in default if c in channels_cfg]

    out, seen = [], set()
    for item in raw:
        for token in str(item).replace("，", ",").split(","):
            token = token.strip()
            if not token:
                continue
            key = _CHANNEL_ALIAS.get(token.lower(), _CHANNEL_ALIAS.get(token, token.lower()))
            if key not in channels_cfg:
                sys.exit(f"不支持的渠道：{token}（可选：{', '.join(channels_cfg)}）")
            if key not in seen:
                out.append(key)
                seen.add(key)
    return out


def build_links(cfg: dict, keyword: str, channels: list[str]) -> list[dict]:
    channels_cfg = cfg.get("channels") or {}
    kw_enc = quote_plus(keyword)
    links = []
    for key in channels:
        blk = channels_cfg.get(key) or {}
        tpl = blk.get("url_template") or ""
        links.append({
            "channel": key,
            "name": blk.get("display_name", key),
            "keyword": keyword,
            "url": tpl.format(kw=kw_enc),
            "note": blk.get("note", ""),
        })
    return sorted(links, key=lambda x: (channels_cfg.get(x["channel"], {}) or {}).get("priority", 99))


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def to_markdown(result: dict) -> str:
    lines = [f"**采购关键词**：`{result['keyword']}`"]
    links = result.get("links") or []
    if links:
        lines.append("")
        lines.append("| 渠道 | 搜索链接 |")
        lines.append("|---|---|")
        for lk in links:
            lines.append(f"| {lk['name']} | [{lk['keyword']}]({lk['url']}) |")
    variants = result.get("keyword_variants") or []
    if variants:
        base = links[0]["url"].split("?")[0] + "?q=" if links else "https://s.taobao.com/search?q="
        lines.append("")
        lines.append("**备选关键词**（主关键词结果太少时可换用）：")
        for v in variants:
            lines.append(f"- [{v}]({base}{quote_plus(v)})")
    return "\n".join(lines)


def _parse_sets(items: list[str] | None) -> dict:
    fields: dict[str, str] = {}
    for item in items or []:
        if "=" in item:
            k, v = item.split("=", 1)
            fields[k.strip()] = v.strip()
        else:
            # 没写 key 的裸值，按物料名处理
            fields["kind"] = item.strip()
    return fields


def main():
    p = argparse.ArgumentParser(
        description="生成采购搜索链接（淘宝 / 天猫 / 1688 / 京东）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--material", "-m", default="generic", help="物料类型，如 lubricant / synchronous_belt")
        sp.add_argument("--set", "-s", dest="sets", action="append", metavar="KEY=VALUE",
                        help="字段赋值，可重复；不写 KEY 时按物料名处理")
        sp.add_argument("--extra", "-e", default="", help="附加自由文本（追加到关键词末尾）")
        sp.add_argument("--channels", "-c", action="append", help="渠道，逗号分隔；默认 taobao,tmall")
        sp.add_argument("--format", "-f", choices=["json", "md"], default="json")

    for name, help_text in (
        ("build", "拼关键词 + 生成各渠道搜索链接"),
        ("keyword", "只拼关键词"),
        ("link", "用现成关键词生成链接"),
        ("materials", "列出可用物料模板"),
        ("channels", "列出可用渠道"),
    ):
        sp = sub.add_parser(name, help=help_text)
        if name == "link":
            sp.add_argument("--keyword", "-k", required=True)
            sp.add_argument("--channels", "-c", action="append")
            sp.add_argument("--format", "-f", choices=["json", "md"], default="json")
        elif name in ("build", "keyword"):
            add_common(sp)

    args = p.parse_args()
    cfg = load_config()

    if args.cmd == "channels":
        out = [{"channel": k, "name": (v or {}).get("display_name", k),
                "url_template": (v or {}).get("url_template", ""),
                "default": bool((v or {}).get("default")),
                "note": (v or {}).get("note", "")}
               for k, v in (cfg.get("channels") or {}).items()]
        out.sort(key=lambda x: x["channel"])
        print(json.dumps({"channels": out}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "materials":
        out = []
        for k, v in (cfg.get("materials") or {}).items():
            if k == "generic":
                continue
            out.append({
                "material": k,
                "display_name": (v or {}).get("display_name", k),
                "keyword_order": (v or {}).get("keyword_order", []),
                "field_hint": (v or {}).get("field_hint", {}),
                "examples": (v or {}).get("examples", []),
                "note": (v or {}).get("note", ""),
            })
        print(json.dumps({"materials": out, "generic_fallback": "generic" in (cfg.get("materials") or {})},
                         ensure_ascii=False, indent=2))
        return

    keyword_seq: list[str] = []
    if args.cmd == "link":
        keyword = args.keyword.strip()
        channels = normalize_channels(cfg, args.channels)
        material_key = "generic"
        block = {}
    else:
        fields = _parse_sets(args.sets)
        material_key, block = resolve_material(cfg, args.material)
        keyword_seq = compose_keyword(block, fields, args.extra)
        if not keyword_seq or not keyword_seq[0]:
            sys.exit("关键词为空：请用 --set kind=<物料名> 至少给出物料名")
        keyword = keyword_seq[0]
        channels = normalize_channels(cfg, args.channels, block)

    links = build_links(cfg, keyword, channels)
    result = {
        "material": material_key,
        "material_name": (cfg.get("materials") or {}).get(material_key, {}).get("display_name", material_key),
        "keyword": keyword,
        "keyword_variants": (keyword_seq if args.cmd == "build" else [])[1:],
        "primary_channel": links[0]["channel"] if links else None,
        "primary_url": links[0]["url"] if links else None,
        "links": links,
        "config_source": str(CONFIG_PATH),
    }
    if args.cmd == "keyword":
        result.pop("links", None)

    if args.format == "md":
        print(to_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

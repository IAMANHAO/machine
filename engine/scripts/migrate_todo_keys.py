#!/usr/bin/env python3
"""一次性迁移：把缓存 frontmatter 里的 `# _todo:` 注释转成真正的 YAML 键。

背景：所有缓存表的待办都写成了注释，程序读不到，于是
`cache_manager report` 的 todo_total 恒为 0，待办追踪实际是失效的。
`_todo` 已经在 mds.knowledge 的 META_KEYS 里，转成真键即可被工具消费。

只改 frontmatter 块（第一对 --- 之间），正文里的注释一律不动。
幂等：已经是真键的文件会被跳过。

    python scripts/migrate_todo_keys.py --dry-run
    python scripts/migrate_todo_keys.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = SKILL_ROOT / "knowledge" / "cache"

_TODO_START = re.compile(r"^#\s*_todo\s*:\s*(.*)$")
_TODO_CONT = re.compile(r"^#\s+(\S.*)$")


def split_frontmatter(lines: list[str]) -> tuple[int, int] | None:
    """返回 frontmatter 内容的 [起, 止) 行号（不含两条 --- 本身）。"""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    return None


def convert(text: str) -> tuple[str, str | None]:
    """返回 (新文本, 提取到的 todo)；无需改动时 todo 为 None。"""
    lines = text.splitlines()
    span = split_frontmatter(lines)
    if not span:
        return text, None
    start, end = span

    if any(re.match(r"^_todo\s*:", ln) for ln in lines[start:end]):
        return text, None  # 已经是真键

    begin = None
    for i in range(start, end):
        if _TODO_START.match(lines[i]):
            begin = i
            break
    if begin is None:
        return text, None

    parts = [_TODO_START.match(lines[begin]).group(1).strip()]
    stop = begin + 1
    while stop < end:
        m = _TODO_CONT.match(lines[stop])
        if not m:
            break
        parts.append(m.group(1).strip())
        stop += 1

    todo = " ".join(p for p in parts if p).strip()
    # YAML 双引号字符串里只需转义 \ 和 "
    escaped = todo.replace("\\", "\\\\").replace('"', '\\"')
    new_lines = lines[:begin] + [f'_todo: "{escaped}"'] + lines[stop:end] + lines[end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), todo


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    changed = 0
    for path in sorted(CACHE_ROOT.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        new_text, todo = convert(text)
        rel = path.relative_to(SKILL_ROOT)
        if todo is None:
            print(f"  跳过  {rel}")
            continue
        changed += 1
        print(f"  转换  {rel}\n        _todo: {todo[:80]}{'…' if len(todo) > 80 else ''}")
        if not args.dry_run:
            path.write_text(new_text, encoding="utf-8")

    print(f"\n{'将转换' if args.dry_run else '已转换'} {changed} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())

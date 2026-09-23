"""
cache_manager.py —— 同步带 / 其它物料的缓存读写与索引维护

提供：

- list_materials()                 列出已缓存的物料
- list_files(material)             列出某物料的 yaml 文件
- read_table(material, file)       读取某 yaml
- write_table(material, file, data, data_source, status, todo=None)
                                  写入 yaml，自动更新 index.yaml
- mark_verified(material, file)    标记某文件已核实
- report(material=None)            生成缓存状态报告（哪些待核验）

工作流：

1. SKILL 引擎阶段 1（检索）调 write_table 保存检索结果
2. SKILL 引擎阶段 3（计算）调 read_table 获取数据
3. 用户阶段性核对后调 mark_verified 标记
4. 调用 report 生成待核验清单

示例：

    python scripts/cache_manager.py report synchronous_belt
    python scripts/cache_manager.py verify synchronous_belt belt_pitch.yaml
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

# 优先 PyYAML；没有则回退到同目录的 yaml_mini（统一通过 _yaml 适配层）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _yaml import load, dump, HAS_PYYAML  # noqa: E402


SKILL_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = SKILL_ROOT / "knowledge"
INDEX_PATH = KNOWLEDGE_DIR / "index.yaml"


def _load_yaml(p: Path):
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return load(f)


def _dump_yaml(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        dump(data, f)


def list_materials() -> list[str]:
    idx = _load_yaml(INDEX_PATH)
    if not idx or "materials" not in idx:
        return []
    return list(idx["materials"].keys())


def list_files(material: str) -> list[str]:
    idx = _load_yaml(INDEX_PATH)
    if not idx or "materials" not in idx:
        return []
    return list(idx["materials"].get(material, {}).get("files", []))


def read_table(material: str, file: str):
    """读取 knowledge/cache/<material>/<file>，返回 dict。"""
    if not file.endswith(".yaml"):
        file = f"{file}.yaml"
    return _load_yaml(KNOWLEDGE_DIR / "cache" / material / file)


def write_table(material: str, file: str, data: dict,
                data_source: str,
                status: str = "single_source",
                todo: list[str] | None = None) -> Path:
    """写入 yaml，自动加 frontmatter、更新 index。"""
    if not file.endswith(".yaml"):
        file = f"{file}.yaml"
    path = KNOWLEDGE_DIR / "cache" / material / file

    front = {
        "data_source": data_source,
        "last_verified": _dt.date.today().isoformat(),
        "verification_status": status,
    }
    if todo:
        front["_todo"] = todo if len(todo) != 1 else todo[0]

    out = {}
    out.update(front)
    out.update(data)

    _dump_yaml(path, out)

    # 更新 index
    idx = _load_yaml(INDEX_PATH) or {
        "schema_version": 1,
        "last_updated": _dt.date.today().isoformat(),
        "materials": {},
    }
    idx["last_updated"] = _dt.date.today().isoformat()
    mat = idx.setdefault("materials", {}).setdefault(material, {})
    mat.setdefault("files", [])
    if file not in mat["files"]:
        mat["files"].append(file)
    _dump_yaml(INDEX_PATH, idx)

    return path


def mark_verified(material: str, file: str, source: str | None = None) -> None:
    """把 yaml 顶部 verification_status 改为 verified，并记录数据源。"""
    if not file.endswith(".yaml"):
        file = f"{file}.yaml"
    path = KNOWLEDGE_DIR / "cache" / material / file
    data = _load_yaml(path)
    if not isinstance(data, dict):
        sys.exit(f"{path} 不是 dict 结构")
    data["verification_status"] = "verified"
    data["last_verified"] = _dt.date.today().isoformat()
    if source:
        data["data_source"] = source
    _dump_yaml(path, data)


def report(material):
    """生成缓存状态报告：哪些待核验。"""
    idx = _load_yaml(INDEX_PATH) or {"materials": {}}
    materials_dict = idx.get("materials", {}) or {}
    if material:
        materials = [material] if material in materials_dict else []
    else:
        materials = list(materials_dict.keys())
    out = {"materials": {}, "todo_total": 0, "verified_total": 0, "single_total": 0}
    for m in materials:
        out["materials"][m] = {}
        for f in list_files(m):
            data = read_table(m, f) or {}
            status = data.get("verification_status", "unknown")
            todo = data.get("_todo")
            out["materials"][m][f] = {
                "status": status,
                "data_source": data.get("data_source", ""),
                "last_verified": data.get("last_verified", ""),
                "todo": todo,
            }
            if status == "verified":
                out["verified_total"] += 1
            else:
                out["single_total"] += 1
            if todo:
                out["todo_total"] += 1
    return out


# ---------------- CLI ----------------

def _cli_report(args):
    r = report(args.material)
    # PyYAML 会把未加引号的日期解析成 datetime.date，json 不认，统一转字符串
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


def _cli_verify(args):
    mark_verified(args.material, args.file, args.source)
    print(f"已标记 {args.material}/{args.file} 为 verified。")


def _cli_list(args):
    if args.material:
        print(f"\n=== {args.material} ===")
        for f in list_files(args.material):
            print(f" - {f}")
    else:
        print("\n=== 已缓存的物料 ===")
        for m in list_materials():
            print(f" - {m} ({len(list_files(m))} 个数据表)")


def _cli_read(args):
    print(json.dumps(read_table(args.material, args.file),
                     ensure_ascii=False, indent=2, default=str))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--yaml", action="store_true",
                   help="若使用 mini-yaml fallback，建议打印此条提示")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_report = sub.add_parser("report")
    p_report.add_argument("material", nargs="?")
    p_report.set_defaults(func=_cli_report)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("material")
    p_verify.add_argument("file")
    p_verify.add_argument("--source", default=None)
    p_verify.set_defaults(func=_cli_verify)

    p_list = sub.add_parser("list")
    p_list.add_argument("material", nargs="?")
    p_list.set_defaults(func=_cli_list)

    p_read = sub.add_parser("read")
    p_read.add_argument("material")
    p_read.add_argument("file")
    p_read.set_defaults(func=_cli_read)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

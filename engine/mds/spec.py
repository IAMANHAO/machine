"""mds.spec —— 工作流规格的加载与校验

workflows/<material>.yaml 是**可执行契约**，workflows/<material>.md 保留为人读文档。
新增一个物料 = 写一份 YAML，不改代码、不重新打包。

规格在加载时就做静态校验：未知 kind、重复 id、表达式引用了不存在的变量，
都在这里报 SpecError，而不是等跑到一半才炸。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import expr as _expr
from .errors import SpecError
from .knowledge import SKILL_ROOT, user_root

STEP_KINDS = {
    "formula",        # 表达式求值
    "table_lookup",   # 按路径精确查表（取标量）
    "table_pick",     # 按路径取一整组推荐字段（润滑油这类决策表用）
    "table_interp",   # 二维插值（凸包外拒绝）
    "bucket",         # 连续量落到命名分档
    "round_to_series",  # 圆整到标准系列
    "row_select",     # 在有序行集里按阈值挑一行，取该行多个字段（选规格/选型号用）
    "check",          # 校核：比较 + 通过与否 + 调整建议
    "select",         # 引擎给候选，人/AI 决策
    "default",        # 取用户输入，缺省时回退到某表达式
    "text",           # 渲染一段模板文本（给结果表/采购关键词用的标签）
}

_CMP = {"<=", "<", ">=", ">", "==", "!="}


@dataclass
class InputDef:
    id: str
    name_zh: str = ""
    unit: str = ""
    type: str = "number"          # number | enum | text
    required: bool = False
    domain: dict = field(default_factory=dict)
    options: list = field(default_factory=list)
    options_from: dict = field(default_factory=dict)
    default: Any = None
    typical: Any = None
    hint: str = ""
    one_of: str = ""              # 同组内二选一（如 i 与 n2）
    # 条件必填：表达式为真时才要求此项。分支型工作流里，
    # 不同分支需要的参数完全不同，一刀切的 required 会逼人填无关的值。
    required_when: str = ""

    def label(self) -> str:
        return f"{self.name_zh or self.id}（{self.unit}）" if self.unit else (self.name_zh or self.id)


@dataclass
class StepDef:
    id: str
    kind: str
    name_zh: str = ""
    outputs: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    note: str = ""
    raw: dict = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self.raw:
            raise SpecError(f"步骤 {self.id}（{self.kind}）缺少必填字段 {key!r}")
        return self.raw[key]


@dataclass
class WorkflowSpec:
    material: str
    name_zh: str = ""
    standard: str = ""
    schema_version: int = 1
    workflow_doc: str = ""
    inputs: list[InputDef] = field(default_factory=list)
    steps: list[StepDef] = field(default_factory=list)
    result: list[dict] = field(default_factory=list)
    procure: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # 数据自检用的探测网格：base 给固定参数，sweep 给要遍历的维度
    probe: dict = field(default_factory=dict)
    path: Path | None = None
    # builtin（随包）/ user（用户自建）/ ai_generated（AI 起草后保存的）
    # ai_generated 的置信度一律按最低档处理，界面、报告、导出件全程打标
    provenance: str = "builtin"
    generated_by: str = ""          # 起草它的模型，便于追溯

    def input(self, iid: str) -> InputDef | None:
        return next((i for i in self.inputs if i.id == iid), None)

    def step(self, sid: str) -> StepDef | None:
        return next((s for s in self.steps if s.id == sid), None)


def _workflow_dirs(root: Path | str | None = None) -> list[Path]:
    """工作流的查找顺序：**内置优先，用户目录兜底**。

    顺序不能反：已随包核验过的工作流不该被用户目录里的同名文件顶掉——
    那会让一个 AI 起草的 🔴 规格悄悄取代一份有信源的 🟡 规格。
    """
    base = Path(root) if root else SKILL_ROOT
    dirs = [base / "workflows"]
    ur = user_root()
    if root is None and ur:
        dirs.append(ur / "workflows")
    return dirs


def load(material: str, root: Path | str | None = None) -> WorkflowSpec:
    """加载 workflows/<material>.yaml。内置找不到时再看用户目录。"""
    dirs = _workflow_dirs(root)
    path = next((d / f"{material}.yaml" for d in dirs
                 if (d / f"{material}.yaml").exists()), None)
    if path is None:
        avail = sorted({p.stem for d in dirs for p in d.glob("*.yaml")})
        raise SpecError(
            f"没有物料 {material!r} 的可执行工作流。"
            f"当前可用：{', '.join(avail) or '无'}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec = parse(data, path=path)
    # 落在用户目录里的一律视为非内置 —— 界面与报告据此打标
    if len(dirs) > 1 and path.parent == dirs[-1] and spec.provenance == "builtin":
        spec.provenance = "user"
    return spec


def available(root: Path | str | None = None) -> list[str]:
    return sorted({p.stem for d in _workflow_dirs(root) for p in d.glob("*.yaml")})


def parse(data: dict, path: Path | None = None) -> WorkflowSpec:
    if not isinstance(data, dict):
        raise SpecError("工作流规格的顶层必须是一个映射")
    if "material" not in data:
        raise SpecError("工作流规格缺少 material 字段")

    inputs = [_parse_input(raw, idx) for idx, raw in enumerate(data.get("inputs") or [])]
    steps = [_parse_step(raw, idx) for idx, raw in enumerate(data.get("steps") or [])]

    spec = WorkflowSpec(
        material=data["material"],
        name_zh=data.get("name_zh", ""),
        standard=data.get("standard", ""),
        schema_version=int(data.get("schema_version", 1)),
        workflow_doc=data.get("workflow_doc", ""),
        inputs=inputs,
        steps=steps,
        result=list(data.get("result") or []),
        procure=dict(data.get("procure") or {}),
        notes=list(data.get("notes") or []),
        probe=dict(data.get("probe") or {}),
        provenance=str(data.get("provenance") or "builtin"),
        generated_by=str(data.get("generated_by") or ""),
        path=path,
    )
    _validate(spec)
    return spec


def _parse_input(raw: dict, idx: int) -> InputDef:
    if not isinstance(raw, dict) or "id" not in raw:
        raise SpecError(f"第 {idx + 1} 个输入项缺少 id")
    known = {f for f in InputDef.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise SpecError(f"输入项 {raw['id']} 含未知字段：{', '.join(sorted(unknown))}")
    return InputDef(**raw)


def _parse_step(raw: dict, idx: int) -> StepDef:
    if not isinstance(raw, dict) or "id" not in raw:
        raise SpecError(f"第 {idx + 1} 个步骤缺少 id")
    kind = raw.get("kind")
    if kind not in STEP_KINDS:
        raise SpecError(
            f"步骤 {raw['id']} 的 kind={kind!r} 未知，可用：{', '.join(sorted(STEP_KINDS))}")
    return StepDef(
        id=raw["id"],
        kind=kind,
        name_zh=raw.get("name_zh", ""),
        outputs=list(raw.get("outputs") or [raw["id"]]),
        source=dict(raw.get("source") or {}),
        note=raw.get("note", ""),
        raw=raw,
    )


def _validate(spec: WorkflowSpec) -> None:
    """静态校验：id 唯一、表达式变量可解析、比较符合法。"""
    seen: set[str] = set()
    for inp in spec.inputs:
        if inp.id in seen:
            raise SpecError(f"输入项 id 重复：{inp.id}")
        seen.add(inp.id)

    # 可用变量集合随步骤推进增长
    known = set(seen)
    step_ids: set[str] = set()
    for step in spec.steps:
        if step.id in step_ids:
            raise SpecError(f"步骤 id 重复：{step.id}")
        step_ids.add(step.id)

        sources_to_check = [(f, step.raw.get(f)) for f in
                            ("expr", "value", "limit", "filter", "fallback", "when")]
        where = step.raw.get("where")
        for cond in ([where] if isinstance(where, dict) else (where or [])):
            if isinstance(cond, dict):
                sources_to_check.append(
                    (f"where.{cond.get('field', '?')}", cond.get("value")))
        for src_field, src in sources_to_check:
            if isinstance(src, str) and _looks_like_expr(src):
                missing = _expr.referenced_names(src) - known
                if missing:
                    raise SpecError(
                        f"步骤 {step.id} 的 {src_field} 引用了此处尚不可用的变量："
                        f"{', '.join(sorted(missing))}")

        if step.kind == "check":
            op = step.get("op", "<=")
            if op not in _CMP:
                raise SpecError(f"步骤 {step.id} 的比较符 {op!r} 非法，可用：{', '.join(sorted(_CMP))}")

        if step.kind == "formula":
            step.require("expr")
        elif step.kind == "table_lookup":
            step.require("table"), step.require("key")
        elif step.kind == "table_pick":
            step.require("table"), step.require("key"), step.require("fields")
        elif step.kind == "table_interp":
            for k in ("table", "group", "rows_key", "x", "y", "z"):
                step.require(k)
        elif step.kind == "bucket":
            step.require("value"), step.require("bins")
        elif step.kind == "round_to_series":
            step.require("table"), step.require("path"), step.require("value")
        elif step.kind == "row_select":
            step.require("table"), step.require("rows_key")
            step.require("where"), step.require("fields")
        elif step.kind == "select":
            step.require("candidates")
        elif step.kind == "default":
            step.require("fallback")
        elif step.kind == "text":
            step.require("template")

        known.update(step.outputs)

    for row in spec.result:
        if "label" not in row or "value" not in row:
            raise SpecError(f"结果表行缺少 label 或 value：{row!r}")


def _looks_like_expr(src: str) -> bool:
    """区分表达式与字面量：'{belt_type}' 这种模板串不是表达式。"""
    return "{" not in src

"""API 数据契约（pydantic）。

前端只认这里定义的形状；引擎的 trace 结构原样透传，不做二次加工——
"不存在第二份展示用数据"是这套架构的核心约束。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    engine_version: str
    skill_root: str
    online: bool = Field(description="网络可达（M4 起由真实探测填充）")
    ai_bound: bool = Field(description="是否已绑定 AI 账号（任意服务商）")
    mode: Literal["online", "offline", "auto"] = "auto"
    workflows: int
    materials: int
    knowledge_writable: bool = True
    packaged: bool = False


class MaterialOut(BaseModel):
    id: str
    name_zh: str
    icon: str = ""
    standard: str = ""
    status: Literal["ready", "cache_only", "planned"]
    # builtin（随包）/ user（用户自建）/ ai_generated（AI 起草后保存的）
    # 前端据此打 🔴 标记——生成的物料没有任何信源，不能与随包的混在一起显示
    # user_guided：引导式选型（有依据、有取证、用户确认过公式）
    # ai_generated：旧版「AI 一次性起草」留下的，只读兼容，不再新建
    provenance: Literal["builtin", "user", "user_guided",
                        "ai_generated"] = "builtin"
    table_count: int = 0
    confidence: str = "unknown"
    workflow_doc: str = ""
    note: str = ""


class OptionOut(BaseModel):
    value: str
    label: str


class InputOut(BaseModel):
    id: str
    name_zh: str
    unit: str = ""
    type: str = "number"
    required: bool = False
    # 条件必填：表达式为真时才必填（分支型工作流用）
    required_when: str = ""
    # 简单条件的结构化形式，供界面显隐分支字段；复杂条件为 None
    depends_on: dict | None = None
    one_of: str = ""
    hint: str = ""
    default: Any = None
    domain: dict = Field(default_factory=dict)
    options: list[OptionOut] = Field(default_factory=list)


class StepOutline(BaseModel):
    id: str
    name_zh: str
    kind: str
    unit: str = ""


class SourceOut(BaseModel):
    table_file: str
    data_source: str = ""
    confidence: str = "unknown"
    last_verified: Any = None


class WorkflowOut(BaseModel):
    material: str
    name_zh: str
    standard: str = ""
    workflow_doc: str = ""
    # user_guided：引导式选型（有依据、有取证、用户确认过公式）
    # ai_generated：旧版「AI 一次性起草」留下的，只读兼容，不再新建
    provenance: Literal["builtin", "user", "user_guided",
                        "ai_generated"] = "builtin"
    generated_by: str = ""
    notes: list[str] = Field(default_factory=list)
    inputs: list[InputOut]
    steps: list[StepOutline]
    sources: list[SourceOut] = Field(default_factory=list)
    confidence: str = "unknown"


class RunIn(BaseModel):
    material: str
    values: dict[str, Any] = Field(default_factory=dict)
    choices: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    save: bool = True


class RunOut(BaseModel):
    trace: dict
    procure: dict | None = None
    procure_error: str | None = None
    project_id: str | None = None


class ProcureIn(BaseModel):
    material_template: str = "generic"
    fields: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] | None = None
    extra: str = ""


class ProjectOut(BaseModel):
    id: str
    material: str
    title: str
    status: str
    summary: str = ""
    headline: str = ""
    confidence: str = "unknown"
    values: dict = Field(default_factory=dict)
    choices: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str
    trace: dict | None = None
    procure: dict | None = None
    stale_sources: list[dict] = Field(default_factory=list)


class ErrorOut(BaseModel):
    error: str
    message: str
    detail: dict = Field(default_factory=dict)

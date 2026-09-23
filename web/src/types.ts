/** 与 server/schemas.py 一一对应。trace 结构直接来自引擎，前端不做二次加工。 */

export type Confidence = 'verified' | 'single_source' | 'self_defined' | 'unknown'
export type RunStatus =
  | 'ok' | 'check_failed' | 'data_missing' | 'needs_choice' | 'no_solution'
export type StepStatus = RunStatus | 'skipped' | 'not_applicable'

export interface Health {
  status: 'ok'
  engine_version: string
  skill_root: string
  online: boolean
  ai_bound: boolean
  mode: 'online' | 'offline' | 'auto'
  workflows: number
  materials: number
}

export interface Material {
  id: string
  name_zh: string
  icon: string
  standard: string
  status: 'ready' | 'cache_only' | 'planned'
  table_count: number
  confidence: Confidence
  workflow_doc: string
  note: string
}

export interface Option { value: string; label: string }

export interface InputDef {
  id: string
  name_zh: string
  unit: string
  type: 'number' | 'enum' | 'text'
  required: boolean
  /** 条件必填：该表达式为真时此项必填（分支型工作流） */
  required_when: string
  /** 简单条件的结构化形式，用于显隐分支字段；复杂条件为 null（则一律显示） */
  depends_on: { field: string; equals: string | number } | null
  one_of: string
  hint: string
  default: unknown
  domain: { min?: number; max?: number }
  options: Option[]
}

export interface Source {
  table_file: string
  data_source: string
  /** 第二信源；有它才允许标 verified */
  second_source?: string
  confidence: Confidence
  last_verified: string | null
  /** 内容指纹，用于判断存档结果是否已过时 */
  fingerprint?: string
}

export interface Workflow {
  material: string
  name_zh: string
  standard: string
  workflow_doc: string
  notes: string[]
  inputs: InputDef[]
  steps: { id: string; name_zh: string; kind: string; unit: string }[]
  sources: Source[]
  confidence: Confidence
}

export interface Candidate {
  value: string
  label: string
  detail?: Record<string, unknown>
}

export interface StepError {
  error: string
  message: string
  gap?: string | null
  /** NoSolution 专有：改设计的方向，以及所需值与系列上限 */
  remedy?: string | null
  required?: number
  limit?: number
  what?: string | null
  table?: string | null
  available?: unknown
  candidates?: Candidate[]
  step?: string
  reason?: string | null
}

export interface Step {
  id: string
  name_zh: string
  kind: string
  status: StepStatus
  formula: string
  substitution: string
  inputs: Record<string, number | string>
  value: number | string | boolean | null
  value_display: string
  unit: string
  outputs: Record<string, unknown>
  source: Partial<Source> & { ref?: string }
  note: string
  detail: {
    passed?: boolean
    op?: string
    value?: number
    limit?: number
    unit?: string
    remedy?: string
    candidates?: Candidate[]
    chosen?: string
    by?: string
    series_range?: [number, number]
    series_size?: number
    mode?: string
    input?: number
  }
  error: StepError | null
}

export interface ResultRow { label: string; value: string; unit: string }

export interface Trace {
  material: string
  name_zh: string
  standard: string
  status: RunStatus
  inputs: Record<string, number | string>
  steps: Step[]
  checks: Step[]
  outputs: Record<string, number | string | boolean>
  result: ResultRow[]
  sources: Source[]
  confidence: Confidence
  warnings: string[]
  blocker: StepError | null
}

export interface ProcureLink {
  channel: string
  name: string
  keyword: string
  url: string
  note: string
}

export interface Procure {
  material_template: string
  keyword: string
  keyword_variants: string[]
  links: ProcureLink[]
  note: string
}

export interface RunResult {
  trace: Trace
  procure: Procure | null
  procure_error: string | null
  project_id: string | null
}

export interface Project {
  id: string
  material: string
  title: string
  status: RunStatus
  summary: string
  headline: string
  confidence: Confidence
  values: Record<string, unknown>
  choices: Record<string, string>
  created_at: string
  updated_at: string
  trace?: Trace
  procure?: Procure | null
  stale_sources?: StaleSource[]
}

export interface ApiError {
  error: string
  message: string
  param?: string
  value?: unknown
  domain?: { min?: number; max?: number }
}

// ── M3 知识库 ────────────────────────────────────────────────────────

export type GapKind =
  | 'frontmatter' | 'fake_green' | 'grid_hole' | 'empty_group'
  | 'unreachable' | 'no_solution'

export type Severity = 'blocking' | 'warning' | 'info'

export interface FillTarget {
  table: string
  group: string
  rows_key: string
  x_field: string
  x_value: number
  y_field: string
  z_field: string
  y_missing: number[]
}

export interface Gap {
  kind: GapKind
  severity: Severity
  table: string
  message: string
  coords: Record<string, unknown>
  fix_hint: string
  /** 补录界面直接可用的定位信息；非数据类缺口为空对象 */
  target: Partial<FillTarget>
}

export interface TableAudit {
  material: string
  name: string
  file: string
  confidence: Confidence
  data_source: string
  second_source: string
  last_verified: string | null
  todo: string
  can_be_verified: boolean
  issues: string[]
}

export type ProbeStatus =
  | 'ok' | 'check_failed' | 'no_solution' | 'data_missing'
  | 'needs_choice' | 'input_error' | 'error'

export interface ProbeCell {
  combo: Record<string, string | number>
  status: ProbeStatus
  message?: string
  gap?: string
  remedy?: string
}

export interface MaterialAudit {
  material: string
  has_workflow: boolean
  tables: TableAudit[]
  gaps: Gap[]
  counts: {
    tables: number
    verified: number
    can_be_verified: number
    blocking: number
    warning: number
    info: number
  }
  probe: { total: number; reachable: number; matrix: ProbeCell[] }
}

export interface AuditAll {
  materials: MaterialAudit[]
  totals: {
    tables: number; verified: number; can_be_verified: number
    blocking: number; warning: number; reachable: number; probed: number
  }
}

export interface TableContent {
  material: string
  name: string
  file: string
  meta: Record<string, unknown>
  data: Record<string, unknown>
  confidence: Confidence
  fingerprint: string
  path: string
}

export interface StaleSource {
  table_file: string
  reason: string
  was?: string
  now?: string
}

// ── M4 账号与 AI ─────────────────────────────────────────────────────

export interface Binding {
  profile: string
  provider: string
  base_url: string
  model: string
  label: string          // 脱敏后的 key，绝不含真实值
  bound_at: string
  models_seen: string[]
}

export interface AiLimits {
  max_tokens_per_call: number
  max_calls_per_day: number
  enabled: boolean
}

export interface UsageToday {
  date: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cache_hit_tokens: number
}

/** 一家服务商的接入事实。**不含任何凭据。** */
export interface ProviderSpec {
  id: string
  name_zh: string
  base_url: string
  console_url: string
  model_hint: string        // 输入框占位示例，不是可用模型清单
  notes: string             // 这家的坑，绑定时必须让用户看到
  has_balance: boolean      // 有没有余额查询接口
  may_list_models: boolean  // 只是"先试一把"的提示，不是断言
  key_env_hint: string
}

/** 已绑定的一家，带上显示用的名字与是否生效。 */
export interface BoundAccount extends Binding {
  name_zh: string
  active: boolean
  key_present: boolean      // 元数据在但凭据库里找不到 key 时为 false
}

/** 绑定验证的结果——**要如实告诉用户这次验证花没花钱**。 */
export interface Validation {
  models: string[]
  model: string
  method: 'models_list' | 'chat_probe'
  cost_hint: string
  usage: { total_tokens: number } | null
}

export interface AccountStatus {
  bound: boolean
  mode: string
  effective_mode: 'online' | 'offline'
  binding: Binding | null
  provider: ProviderSpec | null
  active: string
  bindings: BoundAccount[]
  providers: ProviderSpec[]
  limits: AiLimits
  usage_today: UsageToday
  keyring_available: boolean
  console_url: string
  notice: string
}

export interface Balance {
  available: boolean
  currency?: string
  total_balance?: string
  granted_balance?: string
  topped_up_balance?: string
  is_available?: boolean
  note?: string
}

export interface Usage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cache_hit_tokens: number
  model: string
}

export interface IntentResult {
  material: string | null
  values: Record<string, number | string>
  unmatched: string[]
  notes: string
  source: 'ai' | 'offline' | 'empty'
  usage?: Usage
}

export interface Suggestion {
  value: number | string
  rationale: string
  unit: string
  name_zh: string
}

export interface SuggestResult {
  suggestions: Record<string, Suggestion>
  skipped: Record<string, string>
  /** 没通过参数校验、已被引擎挡下的建议 */
  rejected: Record<string, string>
  source: 'ai' | 'offline' | 'none'
  note?: string
  usage?: Usage
}

export interface ExplainResult {
  text: string
  source: 'ai'
  usage?: Usage
}

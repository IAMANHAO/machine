/** 与 server/schemas.py 一一对应。trace 结构直接来自引擎，前端不做二次加工。 */

export type Confidence = 'verified' | 'single_source' | 'self_defined' | 'unknown'

/**
 * 一份工作流的出身。
 * - `builtin`      随包，有信源标注
 * - `user`         用户自己手写的 YAML
 * - `user_guided`  引导式选型：依据经服务端取证、由用户确认，公式由用户过目确认
 * - `ai_generated` 旧版「AI 一次性起草」，只读兼容，不再新建
 */
export type Provenance = 'builtin' | 'user' | 'user_guided' | 'ai_generated'
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
  /** 出身。user_guided / ai_generated 一律按 🔴 显示 */
  provenance: Provenance
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
  provenance: Provenance
  generated_by: string
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
  /** 清单里没有时，用户想选的物料名。认出来了就是空串。 */
  unknown_material: string
  /** 能不能走引导式选型。离线时恒为 false —— 阶段 1 要真的联网取证 */
  can_guide: boolean
  unmatched: string[]
  notes: string
  source: 'ai' | 'offline' | 'empty'
  usage?: Usage
}

// ── 引导式选型（SKILL.md 阶段 0~6）────────────────────────────────────

/** 一条依据的取证结果。**这是引导式与旧版起草最大的区别。** */
export interface Evidence {
  claim: string
  keys: string[]
  /**
   * - `cross_checked`      两个不同注册域都印证到了
   * - `trusted`            只有一处，但那处是白名单可信站（mechtool.cn）
   * - `single_source`      只有一处，照实标，不阻断
   * - `unverified`         抓到了正文却没有它声称的标准号 → **选不了**
   * - `unverifiable_claim` 提不出可比对的关键词，只能由用户自己核
   */
  status: 'cross_checked' | 'trusted' | 'single_source' | 'unverified'
        | 'unverifiable_claim'
  /** 够不够资格被选为依据往下走 */
  usable: boolean
  domains: string[]
  hits: EvidenceDoc[]
  misses: EvidenceDoc[]
  failures: EvidenceDoc[]
}

export interface EvidenceDoc {
  url: string
  final_url: string
  title: string
  domain: string
  tier: 'trusted' | 'normal' | 'unlisted'
  fetched_at: string
  fingerprint: string
  ok: boolean
  error: string
}

export interface BasisCandidate {
  id: string
  claim: string
  standard: string
  why: string
  outline: string[]
  urls: string[]
  /** 被剔除的引用：不在检索结果集里，即模型凭记忆编的 */
  dropped_urls: string[]
  evidence: Evidence
}

export interface ConfirmedBasis {
  id: string
  claim: string
  standard: string
  outline: string[]
  urls: string[]
  status: string
  domains?: string[]
  confirmed_at: string
  confirmed_by: string
}

/** 引导给出的一个参数。`round` 决定第几轮问，单轮不超过 6 项。 */
export interface GuidedInput {
  id: string
  name_zh: string
  unit: string
  type: 'number' | 'enum' | 'text'
  required: boolean
  round: number
  domain?: { min?: number; max?: number }
  options?: Option[]
  from_handbook?: boolean
  hint: string
}

/** 某个阶段修了几轮才过闸门。**不是可以藏起来的事。** */
export interface RepairAttempt {
  attempt: number
  reasons: string[]
  passed: boolean
  stopped?: string
  usage: Usage
}

/**
 * 兜底档：AI 直接做完的参考草案。
 *
 * **它不是选型结果**：没有经过确定性引擎，每个数都没有出处，
 * 存不成物料，也进不了选型报告。引擎只重算了它自己写的代入式。
 */
export interface AiDraft {
  /** false = 模型没给出结构化表格，只有 text 里的原文 */
  parsed: boolean
  name_zh: string
  standard: string
  given: { label: string; symbol: string; value: string; unit: string; note: string }[]
  steps: AiDraftStep[]
  checks: {
    label: string; criterion: string; substitution: string
    passed: boolean; source: string; note?: string
  }[]
  result: { label: string; value: string; unit: string }[]
  caveats: string[]
  /** 引擎复核它自己算术的结果 —— 不是"公式对不对" */
  arith: { ok: number; mismatch: number; unreadable: number }
  /** 带免责头的完整文本，复制出去也带着 */
  text: string
  truncated: boolean
  usage?: Usage
}

export interface AiDraftStep {
  label: string
  symbol: string
  formula: string
  substitution: string
  value: string
  unit: string
  source: string
  note?: string
  /** ok = 算术对得上；mismatch = 它自己算错了；unreadable = 代入式没法核 */
  arith: 'ok' | 'mismatch' | 'unreadable'
  arith_value?: string
}

export type GuidedStage = 'new' | 'basis' | 'inputs' | 'steps' | 'ready' | 'saved'

export interface GuidedSession {
  id: string
  material_text: string
  stage: GuidedStage
  material: string
  material_id: string
  name_zh: string
  model: string

  search: { rung: string; detail: string; problems: string[]; urls: string[] }
  known_urls: string[]
  candidates: BasisCandidate[]
  usable_candidates: number
  basis: Partial<ConfirmedBasis>
  excerpts: { url: string; title: string; excerpt: string }[]

  inputs: GuidedInput[]
  inputs_confirmed_at: string

  steps: Record<string, unknown>[]
  result: ResultRow[]
  missing_inputs: { id: string; name_zh: string; why: string; where: string }[]
  notes: string[]
  confidence_note: string
  formulas_confirmed_at: string

  repair_log: Record<string, RepairAttempt[]>
  /** 兜底档产出的草案。**有它不等于 can_run**。 */
  ai_draft: AiDraft | Record<string, never>
  has_ai_draft: boolean
  created_at: string
  updated_at: string

  // 服务端算好的"现在能做什么"。前端据此置灰按钮，**但后端仍然会拦**。
  can_choose_basis: boolean
  can_propose_inputs: boolean
  can_propose_steps: boolean
  can_confirm_formulas: boolean
  can_run: boolean
}

/** 模型连着几轮都没过闸门时的错误体。 */
export interface GuidanceRejection extends ApiError {
  reasons: string[]
  repair_log: RepairAttempt[]
  stage: string
}

// ── 搜索服务（引导式阶段 1 的检索供能）────────────────────────────────

export interface SearchProviderSpec {
  id: string
  name_zh: string
  console_url: string
  notes: string
  key_env_hint: string
  endpoint: string
}

export interface SearchBinding {
  profile: string
  provider: string
  label: string
  bound_at: string
  last_hits: number
  name_zh?: string
  active?: boolean
  key_present?: boolean
}

export interface WhitelistSite {
  domain: string
  name_zh: string
  tier: 'trusted' | 'normal'
  note: string
  index_urls: string[]
}

export interface SearchStatus {
  bound: boolean
  search_active: string
  bindings: SearchBinding[]
  providers: SearchProviderSpec[]
  whitelist: WhitelistSite[]
  /** 这台机器上阶段 1 实际会走哪一级 */
  rung: 'binding' | 'whitelist'
  notice: string
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

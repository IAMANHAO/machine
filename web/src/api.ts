import type {
  AccountStatus, AiDraft, AiLimits, AlignResult, ApiError, AuditAll, Balance,
  ExplainResult, FillResult, FixAdvice, GuidedInput, GuidedSession, Health,
  IntentResult, Material, MaterialAudit,
  Procure, Project, RunResult, SearchStatus, Step, SuggestResult, TableContent,
  Workflow,
} from './types'

/** 后端返回的业务错误（422/400），带结构化字段供表单定位。 */
export class RequestError extends Error {
  readonly detail: ApiError
  readonly status: number
  constructor(status: number, detail: ApiError) {
    super(detail.message || `请求失败（${status}）`)
    this.name = 'RequestError'
    this.status = status
    this.detail = detail
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new RequestError(0, {
      error: 'NetworkError',
      message: '连不上本地服务。请确认后端已启动：python -m server',
    })
  }

  if (!res.ok) {
    let detail: ApiError = { error: 'HTTPError', message: `请求失败（${res.status}）` }
    try {
      const body = await res.json()
      if (body?.detail) {
        detail = typeof body.detail === 'string'
          ? { error: 'HTTPError', message: body.detail }
          : body.detail
      }
    } catch {
      /* 响应不是 JSON，沿用默认信息 */
    }
    throw new RequestError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) })

const patch = <T>(path: string, body: unknown) =>
  call<T>(path, { method: 'PATCH', body: JSON.stringify(body) })

const put = <T>(path: string, body: unknown) =>
  call<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export const api = {
  health: (mode = 'auto') => call<Health>(`/health?mode=${mode}`),
  materials: () => call<Material[]>('/materials'),
  workflow: (material: string) => call<Workflow>(`/workflow/${material}`),

  run: (body: {
    material: string
    values: Record<string, unknown>
    choices?: Record<string, string>
    project_id?: string | null
    save?: boolean
    /** 哪些值是 AI 补的（id → 理由）。引擎不认识它，只用来在结果里留下出身 */
    ai_filled?: Record<string, string>
    /** 哪些值是 AI 对齐叫法后改的（id → 对齐后的值） */
    ai_aligned?: Record<string, string>
  }) => post<RunResult>('/selection/run', body),

  projects: () => call<Project[]>('/projects'),
  project: (id: string) => call<Project>(`/projects/${id}`),
  deleteProject: (id: string) =>
    call<{ deleted: string }>(`/projects/${id}`, { method: 'DELETE' }),

  procure: (body: {
    material_template: string
    fields: Record<string, unknown>
    channels?: string[]
  }) => post<Procure>('/procure/links', body),

  // ── 知识库 ──
  audit: (probe = true) => call<AuditAll>(`/knowledge/audit?probe=${probe}`),
  auditMaterial: (m: string, probe = true) =>
    call<MaterialAudit>(`/knowledge/${m}/audit?probe=${probe}`),
  table: (m: string, name: string) =>
    call<TableContent>(`/knowledge/${m}/tables/${name}`),
  verifyTable: (m: string, name: string, second_source: string, verified_by = '') =>
    post<Record<string, string>>(`/knowledge/${m}/tables/${name}/verify`,
      { second_source, verified_by }),
  unverifyTable: (m: string, name: string, reason = '') =>
    post<Record<string, string>>(`/knowledge/${m}/tables/${name}/unverify`, { reason }),
  setSources: (m: string, name: string, body: {
    data_source?: string; second_source?: string; note?: string
  }) => patch<Record<string, string>>(`/knowledge/${m}/tables/${name}/sources`, body),
  upsertPoints: (m: string, name: string, body: {
    group: string; rows_key: string; x_field: string; x_value: number
    y_field: string; z_field: string; points: Record<string, number>[]
  }) => post<Record<string, unknown>>(`/knowledge/${m}/tables/${name}/points`, body),

  // ── 账号（BYOK）──
  account: (mode = 'auto') => call<AccountStatus>(`/account?mode=${mode}`),
  providers: () => call<{ providers: import('./types').ProviderSpec[]; default: string }>(
    '/account/providers'),
  bind: (api_key: string, provider = 'deepseek', model = '', base_url = '') =>
    post<{
      binding: import('./types').Binding
      models: string[]
      validation: import('./types').Validation
      balance: Balance | null
    }>('/account/bind', { api_key, provider, model, base_url }),
  activate: (provider: string) => post<{ active: string }>(
    '/account/activate', { provider }),
  // 不传 provider 就解绑当前生效那家
  unbind: (provider?: string) => call<{ unbound: boolean }>(
    provider ? `/account?provider=${encodeURIComponent(provider)}` : '/account',
    { method: 'DELETE' }),
  setModel: (model: string) => post<{ binding: import('./types').Binding }>(
    '/account/model', { model }),
  balance: () => call<Balance>('/account/balance'),
  setLimits: (l: AiLimits) => put<{ limits: AiLimits }>('/account/limits', l),

  // ── 搜索服务（引导式阶段 1 的检索供能）──
  searchStatus: () => call<SearchStatus>('/account/search'),
  bindSearch: (api_key: string, provider: string) =>
    post<{ binding: import('./types').SearchBinding; name_zh: string }>(
      '/account/search/bind', { api_key, provider }),
  activateSearch: (provider: string) =>
    post<{ search_active: string }>('/account/search/activate', { provider }),
  unbindSearch: (provider?: string) => call<{ unbound: boolean }>(
    provider ? `/account/search?provider=${encodeURIComponent(provider)}`
             : '/account/search', { method: 'DELETE' }),

  // ── 引导式选型（SKILL.md 阶段 0~6）──
  // 每一步都是一次往返：阶段推进由服务端决定，前端只是把它画出来。
  guidedSessions: () => call<{ sessions: Array<{
    id: string; material_text: string; stage: string; updated_at: string
  }> }>('/guided'),
  guidedStart: (material_text: string, mode = 'auto') =>
    post<GuidedSession>('/guided', { material_text, mode }),
  guidedGet: (sid: string) => call<GuidedSession>(`/guided/${sid}`),
  guidedDiscard: (sid: string) =>
    call<{ removed: boolean }>(`/guided/${sid}`, { method: 'DELETE' }),
  // 阶段 1：检索 → AI 挑候选 → 服务端逐条取证
  guidedResearch: (sid: string) =>
    post<GuidedSession>(`/guided/${sid}/research`, {}),
  // 用户拍板选一条依据（或自己填一条）
  guidedChooseBasis: (sid: string, basis_id: string,
                      custom?: Record<string, unknown>) =>
    post<GuidedSession>(`/guided/${sid}/basis`, { basis_id, custom }),
  // 阶段 2
  guidedProposeInputs: (sid: string) =>
    post<GuidedSession>(`/guided/${sid}/inputs`, {}),
  guidedConfirmInputs: (sid: string, inputs?: GuidedInput[]) =>
    post<GuidedSession>(`/guided/${sid}/inputs/confirm`, { inputs }),
  // 阶段 3/4：整套步骤 → 用户一次性确认
  guidedProposeSteps: (sid: string) =>
    post<GuidedSession>(`/guided/${sid}/steps`, {}),
  guidedConfirmFormulas: (sid: string, confirmed: boolean) =>
    post<GuidedSession>(`/guided/${sid}/steps/confirm`, { confirmed }),
  // 落盘之前也要能跑 —— "跑通了才存"的前提
  guidedWorkflow: (sid: string) => call<Workflow>(`/guided/${sid}/workflow`),
  guidedRun: (sid: string, values: Record<string, unknown>,
              choices: Record<string, string> = {},
              ai_filled: Record<string, string> = {},
              ai_aligned: Record<string, string> = {}) =>
    post<RunResult>(`/guided/${sid}/run`,
                    { values, choices, ai_filled, ai_aligned }),
  // 兜底档：AI 直接做完，包括出数。**返回的不是选型结果**——
  // 没经过引擎、每个数都没有出处，存不成物料也进不了选型报告。
  guidedAiDraft: (sid: string) =>
    post<{ draft: AiDraft; session: GuidedSession }>(`/guided/${sid}/ai-draft`, {}),
  guidedSave: (sid: string) =>
    post<{ saved: boolean; material: string; name_zh: string; note: string }>(
      `/guided/${sid}/save`, {}),

  // ── AI 三接口 ──
  intent: (text: string, mode = 'auto') =>
    post<IntentResult>('/ai/intent', { text, mode }),
  // ── 让流程别卡住的三条 ──
  // 补进来的值全都走过与手输相同的校验闸门；对齐只能在给定候选里选。
  fillParams: (body: {
    material?: string; session?: string
    known: Record<string, unknown>; reply?: string
  }) => post<FillResult>('/ai/fill-params', body),
  align: (label: string, value: string,
          candidates: { value: string; label: string }[]) =>
    post<AlignResult>('/ai/align', { label, value, candidates }),
  adviseFix: (body: {
    material?: string; session?: string
    param: string; value: unknown; problem: string
    known: Record<string, unknown>
  }) => post<FixAdvice>('/ai/advise-fix', body),

  deleteSaved: (material: string) =>
    call<{ removed: boolean }>(`/ai/saved/${encodeURIComponent(material)}`,
      { method: 'DELETE' }),
  suggest: (material: string, known: Record<string, unknown>, mode = 'auto') =>
    post<SuggestResult>('/ai/suggest', { material, known, mode }),
  explain: (material: string, step: Step, mode = 'auto') =>
    post<ExplainResult>('/ai/explain', { material, step, mode }),

  // ── 导出 ──
  // 服务端会重跑一遍引擎再渲染，所以导出件必定与当前数据一致，
  // 而不是把界面上看到的东西原样打印出去。
  exportReport: async (fmt: 'pdf' | 'xlsx', body: {
    material: string
    values: Record<string, unknown>
    choices?: Record<string, string>
  }): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`/api/export/${fmt}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      let detail: ApiError = { error: 'HTTPError', message: `导出失败（${res.status}）` }
      try {
        const b = await res.json()
        if (b?.detail) detail = typeof b.detail === 'string'
          ? { error: 'HTTPError', message: b.detail } : b.detail
      } catch { /* 二进制响应之外的解析失败，沿用默认信息 */ }
      throw new RequestError(res.status, detail)
    }
    const cd = res.headers.get('content-disposition') || ''
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd)
    const filename = m ? decodeURIComponent(m[1]) : `选型报告.${fmt}`
    return { blob: await res.blob(), filename }
  },
}

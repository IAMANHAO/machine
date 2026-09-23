import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, RequestError } from '../api'
import { saveBlob } from '../download'
import CalcCard from '../components/CalcCard'
import SidePanel from '../components/SidePanel'
import StepNav, { stageStates } from '../components/StepNav'
import { Alert, Badge, ConfidenceBadge, Empty, Section, Spinner } from '../components/ui'
import type {
  ApiError, Health, InputDef, Procure, Project, StaleSource, Trace, Workflow,
} from '../types'

interface Props {
  material: string
  initialProject?: Project | null
  health: Health | null
  /** 首页意图解析识别出来的参数，用于预填阶段 2 */
  seed?: Record<string, unknown> | null
  mode: string
  onGoSettings: () => void
  onGoKnowledge: () => void
}

export default function Workbench({ material, initialProject, health, seed, mode,
                                   onGoSettings, onGoKnowledge }: Props) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [trace, setTrace] = useState<Trace | null>(null)
  const [procure, setProcure] = useState<Procure | null>(null)
  const [projectId, setProjectId] = useState<string | null>(null)
  const [stage, setStage] = useState(2)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  // 存档之后数据表被改过的话，这份结果就不再代表当前知识库
  const [stale, setStale] = useState<StaleSource[]>([])

  // 切换物料：拉取工作流定义，并用上次的项目值（如果有）预填
  useEffect(() => {
    setWorkflow(null); setTrace(null); setProcure(null); setError(null)
    api.workflow(material)
      .then(wf => {
        setWorkflow(wf)
        const init: Record<string, string> = {}
        for (const i of wf.inputs) {
          const from = initialProject?.values?.[i.id] ?? seed?.[i.id] ?? i.default
          if (from !== null && from !== undefined && from !== '') init[i.id] = String(from)
        }
        setValues(init)
        setChoices((initialProject?.choices as Record<string, string>) ?? {})
        setProjectId(initialProject?.id ?? null)
        if (initialProject?.trace) {
          setTrace(initialProject.trace)
          // 采购链接由后端从存档 trace 重算，重开项目时阶段 6 不该是锁的
          setProcure(initialProject.procure ?? null)
          setStale(initialProject.stale_sources ?? [])
          setStage(initialProject.trace.status === 'ok' ? 5 : 3)
        } else {
          setStage(2)
        }
      })
      .catch((e: RequestError) => setError(e.detail))
  }, [material, initialProject, seed])

  const execute = useCallback(async (nextChoices?: Record<string, string>) => {
    setRunning(true); setError(null)
    const payload = nextChoices ?? choices
    try {
      const res = await api.run({
        material,
        values: coerce(values, workflow),
        choices: payload,
        project_id: projectId,
      })
      setTrace(res.trace)
      setProcure(res.procure)
      setProjectId(res.project_id)
      setStale([])  // 刚算完，结果与当前数据一致
      setStage(res.trace.status === 'ok' ? 5 : 3)
    } catch (e) {
      const err = e as RequestError
      setError(err.detail)
      setStage(2)
    } finally {
      setRunning(false)
    }
  }, [material, values, choices, projectId, workflow])

  const onChoose = useCallback((stepId: string, value: string) => {
    const next = { ...choices, [stepId]: value }
    setChoices(next)
    void execute(next)
  }, [choices, execute])

  const states = useMemo(() => stageStates(trace, procure), [trace, procure])

  if (error && !workflow) {
    return <Alert tone="err" title="打不开这个工作流">{error.message}</Alert>
  }
  if (!workflow) return <Spinner label="正在加载工作流规格…" />

  return (
    <div className="grid lg:grid-cols-[210px_1fr_320px] gap-4">
      <StepNav workflow={workflow} stage={stage} onStage={setStage} states={states} />

      <div className="min-w-0">
        {stale.length > 0 && (
          <div className="mb-4">
            <Alert tone="warn" title="这份结果是旧数据算出来的">
              存档之后，它引用的{' '}
              <span className="num">{stale.map(s => s.table_file).join('、')}</span>{' '}
              已被修改。下面显示的仍是当时的计算结果，与当前知识库不一致。
              <div className="mt-2">
                <button className="btn btn-p text-[12px] py-1 px-2.5"
                        onClick={() => void execute()} disabled={running}>
                  {running ? '重算中…' : '按当前数据重算'}
                </button>
              </div>
            </Alert>
          </div>
        )}
        {stage === 0 && <Stage0 wf={workflow} onNext={() => setStage(1)} />}
        {stage === 1 && <Stage1 wf={workflow} onNext={() => setStage(2)} onGoKnowledge={onGoKnowledge} />}
        {stage === 2 && (
          <Stage2 wf={workflow} values={values} setValues={setValues}
                  error={error} running={running} onRun={() => void execute()} />
        )}
        {stage === 3 && <Stage3 trace={trace} onChoose={onChoose} running={running}
                                onBack={() => setStage(2)} onNext={() => setStage(4)} />}
        {stage === 4 && <Stage4 trace={trace} onBack={() => setStage(3)} onNext={() => setStage(5)}
                                onFix={() => setStage(2)} />}
        {stage === 5 && (
          <Stage5 trace={trace} material={material}
                  values={coerce(values, workflow)} choices={choices}
                  onBack={() => setStage(4)} onNext={() => setStage(6)} />
        )}
        {stage === 6 && <Stage6 procure={procure} trace={trace} onBack={() => setStage(5)} />}
      </div>

      <SidePanel sources={workflow.sources} trace={trace} health={health}
                 material={material} values={values} stage={stage} mode={mode}
                 onApplySuggestions={patch => {
                   // AI 的建议只是填进表单，仍要按「开始计算」才生效——
                   // 也就是说它走的是和手输完全一样的那条路
                   setValues(v => ({ ...v, ...patch }))
                   setStage(2)
                 }}
                 onGoSettings={onGoSettings} />
    </div>
  )
}

/** 表单里都是字符串，交给后端前转成数值；空串一律丢掉，让引擎按"未提供"处理。 */
function coerce(values: Record<string, string>, wf: Workflow | null): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(values)) {
    if (v === '' || v === null || v === undefined) continue
    const def = wf?.inputs.find(i => i.id === k)
    out[k] = def && def.type === 'number' && v.trim() !== '' && !Number.isNaN(Number(v))
      ? Number(v) : v
  }
  return out
}

// ── 阶段 0：物料识别 ───────────────────────────────────────────────

function Stage0({ wf, onNext }: { wf: Workflow; onNext: () => void }) {
  return (
    <Section title="阶段 0 · 物料识别" sub="确认要选的物料，以及将依据哪份标准">
      {wf.provenance === 'ai_generated' && (
        <div className="mb-4">
          <Alert tone="err" title="这份选型流程由 AI 起草，未经任何核验">
            引擎只保证它<b>格式合法</b>、且<b>没有携带编造的数据表</b>——
            所有需要查手册的量都做成了输入项，由你自己填。
            <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
              但<b>没有人核对过这些公式是否适用于你的工况</b>。
              起草模型：<span className="num">{wf.generated_by || '未知'}</span>。
              正式设计前请对照手册逐项确认，<b>不要拿这份结果直接定稿</b>。
            </div>
          </Alert>
        </div>
      )}
      <div className="card p-5">
        <div className="grid md:grid-cols-2 gap-4 text-[13px]">
          <Field label="物料">{wf.name_zh}</Field>
          <Field label="主导标准">{wf.standard || '—'}</Field>
          <Field label="可执行规格">
            <span className="num">
              {wf.provenance === 'ai_generated' ? '用户目录/' : ''}workflows/{wf.material}.yaml
            </span>
          </Field>
          <Field label="人读文档"><span className="num">{wf.workflow_doc || '—'}</span></Field>
          <Field label="计算步骤">{wf.steps.length} 步</Field>
          <Field label="数据表">{wf.sources.length} 张 · <ConfidenceBadge level={wf.confidence} /></Field>
        </div>
        <div className="flex pt-4 mt-4 border-t" style={{ borderColor: 'var(--line)' }}>
          <div className="flex-1" />
          <button className="btn btn-p" onClick={onNext}>确认，查看依据 →</button>
        </div>
      </div>
    </Section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] mb-1" style={{ color: 'var(--sub)' }}>{label}</div>
      <div>{children}</div>
    </div>
  )
}

// ── 阶段 1：依据检索 ───────────────────────────────────────────────

function Stage1({ wf, onNext, onGoKnowledge }: {
  wf: Workflow; onNext: () => void; onGoKnowledge: () => void
}) {
  return (
    <Section title="阶段 1 · 依据检索"
             sub="全部命中本地缓存，无需联网"
             right={<Badge kind="info">缓存命中 {wf.sources.length} / {wf.sources.length}</Badge>}>
      <div className="card overflow-hidden mb-4">
        <div className="overflow-x-auto scroll">
          <table className="tbl">
            <thead><tr><th>数据表</th><th>来源</th><th>状态</th><th>最近核验</th></tr></thead>
            <tbody>
              {wf.sources.map(s => (
                <tr key={s.table_file}>
                  <td className="num">{s.table_file}</td>
                  <td>{s.data_source}</td>
                  <td><ConfidenceBadge level={s.confidence} /></td>
                  <td className="num">{s.last_verified ? String(s.last_verified) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {wf.notes.length > 0 && (
        <div className="mb-4">
          <Alert tone="warn" title="本工作流的已知偏差与限制">
            <ul className="list-disc ml-4 space-y-1.5 mt-1">
              {wf.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </Alert>
        </div>
      )}

      <div className="flex gap-2">
        <button className="btn" onClick={onGoKnowledge}>查看知识库</button>
        <div className="flex-1" />
        <button className="btn btn-p" onClick={onNext}>填写工况参数 →</button>
      </div>
    </Section>
  )
}

// ── 阶段 2：参数引导 ───────────────────────────────────────────────

function Stage2({ wf, values, setValues, error, running, onRun }: {
  wf: Workflow
  values: Record<string, string>
  setValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  error: ApiError | null
  running: boolean
  onRun: () => void
}) {
  const set = (id: string, v: string) => setValues({ ...values, [id]: v })

  // 分支型工作流：只显示当前分支用得到的字段。条件由服务端解析好传下来，
  // 前端不重复实现表达式求值——同一套规则两处实现迟早会对不上。
  const applies = (i: InputDef) =>
    !i.depends_on || String(values[i.depends_on.field] ?? '') === String(i.depends_on.equals)
  const shown = wf.inputs.filter(applies)

  const required = shown.filter(i => i.required || i.required_when || i.one_of)
  const optional = shown.filter(i => !i.required && !i.required_when && !i.one_of)
  const hidden = wf.inputs.length - shown.length
  const missing = required.filter(i => !values[i.id] && !i.one_of).length

  return (
    <Section title="阶段 2 · 参数引导"
             sub={`依据 ${wf.standard} 的选型流程，请补充以下工况参数`}
             right={missing > 0 ? <Badge kind="warn">缺失 {missing} 项</Badge>
                                : <Badge kind="ok">参数齐备</Badge>}>
      <div className="card p-5">
        {error && (
          <div className="mb-4">
            <Alert tone="err" title="参数有问题">{error.message}</Alert>
          </div>
        )}

        <div className="text-[12px] font-medium mb-2" style={{ color: '#93c5fd' }}>必需参数</div>
        <div className="grid md:grid-cols-2 gap-3 mb-5">
          {required.map(i => (
            <InputRow key={i.id} def={i} value={values[i.id] ?? ''} onChange={v => set(i.id, v)}
                      invalid={error?.param === i.id} />
          ))}
        </div>

        {hidden > 0 && (
          <div className="text-[11px] mb-4 -mt-2" style={{ color: 'var(--sub)' }}>
            另有 {hidden} 项参数属于其它工况分支，已按你的选择隐藏。
          </div>
        )}

        {optional.length > 0 && (
          <>
            <div className="text-[12px] font-medium mb-2" style={{ color: 'var(--sub)' }}>
              可选参数（不填则按工作流推荐式取值）
            </div>
            <div className="grid md:grid-cols-2 gap-3 mb-5">
              {optional.map(i => (
                <InputRow key={i.id} def={i} value={values[i.id] ?? ''} onChange={v => set(i.id, v)}
                          invalid={error?.param === i.id} />
              ))}
            </div>
          </>
        )}

        <div className="flex gap-2 pt-3 border-t" style={{ borderColor: 'var(--line)' }}>
          <div className="flex-1" />
          <button className="btn btn-p" onClick={onRun} disabled={running}>
            {running ? '计算中…' : '开始计算 →'}
          </button>
        </div>
      </div>
    </Section>
  )
}

function InputRow({ def, value, onChange, invalid }: {
  def: InputDef; value: string; onChange: (v: string) => void; invalid?: boolean
}) {
  const hint = [
    def.hint,
    def.domain.min !== undefined && def.domain.max !== undefined
      ? `取值范围 ${def.domain.min} ~ ${def.domain.max}` : '',
    def.one_of ? '与同组参数二选一' : '',
  ].filter(Boolean).join(' · ')

  return (
    <div>
      <label className="text-[12px] block mb-1.5">
        {def.name_zh}{' '}
        <span style={{ color: 'var(--sub)' }} className="num">
          {def.id}{def.unit ? `（${def.unit}）` : ''}
        </span>
      </label>
      {def.type === 'enum' ? (
        <select className={`inp ${invalid ? 'err' : ''}`} value={value}
                onChange={e => onChange(e.target.value)}>
          <option value="">— 请选择 —</option>
          {def.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      ) : (
        <input className={`inp num ${invalid ? 'err' : ''}`} value={value} inputMode="decimal"
               placeholder={def.default != null ? `默认 ${def.default}` : '请输入'}
               onChange={e => onChange(e.target.value)} />
      )}
      {hint && <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>{hint}</div>}
    </div>
  )
}

// ── 阶段 3：分步计算 ───────────────────────────────────────────────

function Stage3({ trace, onChoose, running, onBack, onNext }: {
  trace: Trace | null
  onChoose: (s: string, v: string) => void
  running: boolean
  onBack: () => void
  onNext: () => void
}) {
  if (running) return <Spinner label="引擎计算中…" />
  if (!trace) return <Empty>还没有计算结果。请先在阶段 2 填写参数。</Empty>

  // text 是给结果表凑标签的辅助步骤，不是推导过程，不占计算卡片
  const calc = trace.steps.filter(
    s => s.kind !== 'check' && s.kind !== 'text'
      && s.status !== 'skipped' && s.status !== 'not_applicable')
  const skipped = trace.steps.filter(s => s.status === 'skipped').length
  const inapplicable = trace.steps.filter(s => s.status === 'not_applicable').length

  return (
    <Section title="阶段 3 · 分步计算"
             right={<Badge kind="info">每一步均可追溯信源</Badge>}>
      {calc.map((s, idx) => (
        <CalcCard key={s.id} step={s} index={idx + 1} onChoose={onChoose} />
      ))}

      {skipped > 0 && (
        <div className="text-[12px] mb-3 px-1" style={{ color: 'var(--sub)' }}>
          还有 {skipped} 步因上面的问题未执行。解决后会自动继续。
        </div>
      )}
      {inapplicable > 0 && (
        <div className="text-[12px] mb-3 px-1" style={{ color: 'var(--sub)' }}>
          另有 {inapplicable} 步属于其它工况分支，本次用不到（不是出错）。
        </div>
      )}

      <div className="flex gap-2">
        <button className="btn" onClick={onBack}>← 改参数</button>
        <div className="flex-1" />
        <button className="btn btn-p" onClick={onNext} disabled={trace.checks.length === 0}>
          进入校核 →
        </button>
      </div>
    </Section>
  )
}

// ── 阶段 4：校核 ───────────────────────────────────────────────────

function Stage4({ trace, onBack, onNext, onFix }: {
  trace: Trace | null; onBack: () => void; onNext: () => void; onFix: () => void
}) {
  if (!trace) return <Empty>还没有计算结果。</Empty>
  const checks = trace.checks
  const passed = checks.filter(c => c.detail.passed).length
  const skipped = trace.steps.filter(s => s.kind === 'check' && s.status === 'skipped')
  const allPassed = checks.length > 0 && passed === checks.length && skipped.length === 0

  return (
    <Section title="阶段 4 · 校核"
             right={<Badge kind={allPassed ? 'ok' : 'warn'}>{passed} / {checks.length} 项通过</Badge>}>
      {checks.map(c => (
        <div key={c.id} className="card p-4 mb-2 flex items-start justify-between gap-3"
             style={c.detail.passed ? undefined : { borderColor: 'rgba(239,68,68,.4)' }}>
          <div className="min-w-0">
            <div className="text-[13px]">
              {c.name_zh}　<span className="num">{c.substitution}</span>
              {c.detail.unit ? <span className="num"> {c.detail.unit}</span> : null}
            </div>
            {c.source?.ref && (
              <div className="text-[11px] mt-0.5" style={{ color: 'var(--sub)' }}>依据：{c.source.ref}</div>
            )}
            {!c.detail.passed && c.detail.remedy && (
              <div className="text-[12px] mt-1.5" style={{ color: '#fbbf24' }}>→ {c.detail.remedy}</div>
            )}
          </div>
          <span className={`badge ${c.detail.passed ? 'b-ok' : 'b-err'} whitespace-nowrap`}>
            {c.detail.passed ? '✅ 通过' : '❌ 不通过'}
          </span>
        </div>
      ))}

      {skipped.length > 0 && (
        <div className="card p-4 mb-2" style={{ color: 'var(--sub)' }}>
          <div className="text-[13px]">另有 {skipped.length} 项校核因流程中断未执行：</div>
          <div className="text-[12px] mt-1">{skipped.map(s => s.name_zh).join('、')}</div>
        </div>
      )}

      <div className="card p-4 mb-4"
           style={{ borderColor: allPassed ? 'rgba(16,185,129,.4)' : 'rgba(245,158,11,.4)' }}>
        <div className="text-[13px]" style={{ color: allPassed ? '#34d399' : '#fbbf24' }}>
          {allPassed ? '✅ 全部校核通过，可进入结果输出'
                     : '⚠ 尚有校核未通过或未执行，结果不可直接用于生产'}
        </div>
      </div>

      <div className="flex gap-2">
        <button className="btn" onClick={onBack}>← 上一步</button>
        <div className="flex-1" />
        {!allPassed && <button className="btn" onClick={onFix}>回到参数调整</button>}
        <button className="btn btn-p" onClick={onNext} disabled={trace.result.length === 0}>
          查看结果 →
        </button>
      </div>
    </Section>
  )
}

// ── 阶段 5：结果输出 ───────────────────────────────────────────────

function Stage5({ trace, material, values, choices, onBack, onNext }: {
  trace: Trace | null
  material: string
  values: Record<string, unknown>
  choices: Record<string, string>
  onBack: () => void
  onNext: () => void
}) {
  const [exporting, setExporting] = useState('')
  const [exportErr, setExportErr] = useState('')

  const download = async (fmt: 'pdf' | 'xlsx') => {
    setExporting(fmt); setExportErr('')
    try {
      const { blob, filename } = await api.exportReport(fmt, { material, values, choices })
      saveBlob(blob, filename)
    } catch (e) {
      setExportErr((e as RequestError).message)
    } finally { setExporting('') }
  }

  if (!trace || trace.result.length === 0) {
    return <Empty>流程未走完，暂无结果表。</Empty>
  }
  const calc = trace.steps.filter(
    s => s.kind !== 'check' && s.kind !== 'text'
      && s.status === 'ok' && s.value !== null)

  return (
    <Section title="阶段 5 · 结果输出"
             right={
               <div className="flex gap-2">
                 <button className="btn" disabled={!!exporting}
                         onClick={() => void download('pdf')}>
                   {exporting === 'pdf' ? '生成中…' : '导出 PDF'}
                 </button>
                 <button className="btn" disabled={!!exporting}
                         onClick={() => void download('xlsx')}>
                   {exporting === 'xlsx' ? '生成中…' : '导出 Excel'}
                 </button>
               </div>
             }>
      {exportErr && <div className="mb-3"><Alert tone="err" title="导出失败">{exportErr}</Alert></div>}
      <div className="card p-4 mb-4">
        <div className="text-[12px] font-medium mb-2" style={{ color: '#93c5fd' }}>表 1 · 计算过程汇总</div>
        <div className="overflow-x-auto scroll">
          <table className="tbl">
            <thead>
              <tr><th>序号</th><th>项目</th><th>符号</th><th>计算值</th><th>单位</th><th>公式 / 依据</th></tr>
            </thead>
            <tbody>
              {calc.map((s, i) => (
                <tr key={s.id}>
                  <td className="num">{i + 1}</td>
                  <td>{s.name_zh}</td>
                  <td className="num">{s.id}</td>
                  <td className="num">{s.value_display}</td>
                  <td>{s.unit || '—'}</td>
                  <td className="text-[12px]">{s.source?.ref || s.formula}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card p-4 mb-4">
        <div className="text-[12px] font-medium mb-2" style={{ color: '#93c5fd' }}>表 2 · 最终选型结果</div>
        <table className="tbl">
          <tbody>
            {trace.result.map(r => (
              <tr key={r.label}>
                <td style={{ width: 150, color: 'var(--sub)' }}>{r.label}</td>
                <td className="num">{r.value}{r.unit ? ` ${r.unit}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card p-4 mb-4">
        <div className="text-[12px] font-medium mb-2" style={{ color: '#93c5fd' }}>
          信源清单 · 整体置信度 <ConfidenceBadge level={trace.confidence} />
        </div>
        <table className="tbl">
          <tbody>
            {trace.sources.map(s => (
              <tr key={s.table_file}>
                <td className="num" style={{ width: 200 }}>{s.table_file}</td>
                <td>{s.data_source}</td>
                <td style={{ width: 120 }}><ConfidenceBadge level={s.confidence} compact /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {trace.warnings.map((w, i) => (
        <div key={i} className="mb-4"><Alert tone="warn" title="风险提示">{w}</Alert></div>
      ))}

      <div className="flex gap-2">
        <button className="btn" onClick={onBack}>← 上一步</button>
        <div className="flex-1" />
        <button className="btn btn-p" onClick={onNext}>采购链接 →</button>
      </div>
    </Section>
  )
}

// ── 阶段 6：采购链接 ───────────────────────────────────────────────

function Stage6({ procure, trace, onBack }: {
  procure: Procure | null; trace: Trace | null; onBack: () => void
}) {
  if (!procure) {
    return (
      <Section title="阶段 6 · 采购链接">
        <Alert tone="warn" title="还没有可采购的结果">
          选型未完成，无法生成采购关键词。
          {trace?.blocker && <> 当前卡在：{trace.blocker.message}</>}
        </Alert>
        <div className="mt-4"><button className="btn" onClick={onBack}>← 上一步</button></div>
      </Section>
    )
  }

  return (
    <Section title="阶段 6 · 采购链接"
             right={<Badge kind="info">由 procure_link 生成</Badge>}>
      <div className="card p-4 mb-4">
        <div className="text-[12px] mb-3" style={{ color: 'var(--sub)' }}>
          采购关键词：<span className="num" style={{ color: 'var(--txt)' }}>{procure.keyword}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {procure.links.map(l => (
            <a key={l.channel} className="btn" href={l.url} target="_blank" rel="noreferrer noopener"
               title={l.note}>
              {l.name} ↗
            </a>
          ))}
        </div>

        {procure.keyword_variants.length > 0 && (
          <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--line)' }}>
            <div className="text-[12px] mb-2" style={{ color: 'var(--sub)' }}>
              备选关键词（主关键词结果太少时可换用）
            </div>
            <div className="flex flex-wrap gap-2">
              {procure.keyword_variants.map(v => (
                <span key={v} className="kbd">{v}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="mb-4"><Alert tone="warn" title="采购提醒">{procure.note}</Alert></div>

      <div className="flex gap-2">
        <button className="btn" onClick={onBack}>← 上一步</button>
        <div className="flex-1" />
        <button className="btn btn-ok"
                onClick={() => navigator.clipboard?.writeText(procure.keyword)}>
          复制关键词
        </button>
      </div>
    </Section>
  )
}

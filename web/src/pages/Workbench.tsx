import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, RequestError } from '../api'
import { saveBlob } from '../download'
import CalcCard from '../components/CalcCard'
import SidePanel from '../components/SidePanel'
import StepNav, { stageStates } from '../components/StepNav'
import { Alert, Badge, ConfidenceBadge, Empty, Section, Spinner } from '../components/ui'
import type {
  ApiError, Candidate, FillResult, FixAdvice, Health, InputDef, Procure, Project,
  StaleSource, Trace, Workflow,
} from '../types'

interface Props {
  material: string
  initialProject?: Project | null
  health: Health | null
  /** 首页意图解析识别出来的参数，用于预填阶段 2 */
  seed?: Record<string, unknown> | null
  mode: string
  /**
   * 引导式选型的会话 id。非空时这份工作流**还没落盘**：
   * 规格从会话里取、计算也走会话的接口。这是为了守住"跑通了才存"——
   * 一份没跑通的流程存下来，只会在物料列表里留一个点进去就报错的入口。
   */
  guidedSid?: string | null
  onGoSettings: () => void
  onGoKnowledge: () => void
}

export default function Workbench({ material, initialProject, health, seed, mode,
                                   guidedSid, onGoSettings, onGoKnowledge }: Props) {
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
  // 哪些值不是用户填的。**这两个 map 是"出身"的唯一记录**：
  // 它们跟着 run 一起送上去，服务端据此在结果警告里如实列出来。
  const [aiFilled, setAiFilled] = useState<Record<string, string>>({})
  const [aiAligned, setAiAligned] = useState<Record<string, string>>({})
  const [assist, setAssist] = useState<FillResult | null>(null)
  const [advice, setAdvice] = useState<FixAdvice | null>(null)
  const [busy, setBusy] = useState('')
  const [reply, setReply] = useState('')

  // 切换物料：拉取工作流定义，并用上次的项目值（如果有）预填
  useEffect(() => {
    setWorkflow(null); setTrace(null); setProcure(null); setError(null)
    ;(guidedSid ? api.guidedWorkflow(guidedSid) : api.workflow(material))
      .then(wf => {
        setWorkflow(wf)
        const init: Record<string, string> = {}
        for (const i of wf.inputs) {
          const from = initialProject?.values?.[i.id] ?? seed?.[i.id] ?? i.default
          if (from !== null && from !== undefined && from !== '') init[i.id] = String(from)
        }
        setValues(init)
        setAiFilled({}); setAiAligned({}); setAssist(null); setAdvice(null)
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
  }, [material, initialProject, seed, guidedSid])

  const execute = useCallback(async (nextChoices?: Record<string, string>) => {
    setRunning(true); setError(null)
    const payload = nextChoices ?? choices
    try {
      // 引导式的规格还没落盘，按 id 加载不到它，所以走会话自己的执行接口。
      // **两条路跑的是同一个 runner**，阶段 3~6 的行为逐字相同。
      const res = guidedSid
        ? await api.guidedRun(guidedSid, coerce(values, workflow), payload,
                              aiFilled, aiAligned)
        : await api.run({
            material,
            values: coerce(values, workflow),
            choices: payload,
            project_id: projectId,
            ai_filled: aiFilled,
            ai_aligned: aiAligned,
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
  }, [material, values, choices, projectId, workflow, guidedSid,
      aiFilled, aiAligned])

  /** 参数没填完就想算：让 AI 把能负责任地补的补上，补不了的问出来。 */
  const fill = useCallback(async (userReply = '') => {
    setBusy('fill'); setError(null)
    try {
      const out = await api.fillParams({
        ...(guidedSid ? { session: guidedSid } : { material }),
        known: coerce(values, workflow), reply: userReply,
      })
      setAssist(out)
      if (Object.keys(out.filled).length > 0) {
        setValues(v => {
          const next = { ...v }
          for (const [id, f] of Object.entries(out.filled)) next[id] = String(f.value)
          return next
        })
        setAiFilled(m => {
          const next = { ...m }
          for (const [id, f] of Object.entries(out.filled)) next[id] = f.rationale
          return next
        })
      }
      setReply('')
    } catch (e) {
      setError((e as RequestError).detail)
    } finally { setBusy('') }
  }, [material, guidedSid, values, workflow])

  /** 某个参数没过校验：问 AI 该怎么改，而不是把人堵在报错上。 */
  const askFix = useCallback(async (param: string) => {
    setBusy('fix')
    try {
      setAdvice(await api.adviseFix({
        ...(guidedSid ? { session: guidedSid } : { material }),
        param, value: values[param] ?? null,
        problem: error?.message ?? '', known: coerce(values, workflow),
      }))
    } catch (e) {
      setError((e as RequestError).detail)
    } finally { setBusy('') }
  }, [material, guidedSid, values, workflow, error])

  /** 叫法对不上：问 AI 用户说的是候选里的哪一个。**只能在候选里指一个。** */
  const align = useCallback(async (stepId: string, label: string, given: string,
                                   candidates: Candidate[]) => {
    setBusy('align')
    try {
      const out = await api.align(label, given,
        candidates.map(c => ({ value: c.value, label: c.label })))
      if (out.value) {
        setAiAligned(m => ({ ...m, [stepId]: out.value as string }))
        const next = { ...choices, [stepId]: out.value }
        setChoices(next)
        await execute(next)
      } else {
        setError({ error: 'AlignFailed',
                   message: `AI 也判断不了「${given}」是哪一个：${out.why}　请你从候选里选一个。` })
      }
    } catch (e) {
      setError((e as RequestError).detail)
    } finally { setBusy('') }
  }, [choices, execute])

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
                  error={error} running={running} onRun={() => void execute()}
                  aiBound={!!health?.ai_bound} aiFilled={aiFilled} busy={busy}
                  assist={assist} advice={advice} reply={reply} setReply={setReply}
                  onFill={r => void fill(r)} onAskFix={p => void askFix(p)}
                  onApplyFix={(pid, v) => {
                    // 采用建议值也要留下出身 —— 它和用户自己敲进去的不是一回事
                    setValues(x => ({ ...x, [pid]: String(v) }))
                    setAiFilled(m => ({ ...m, [pid]: advice?.how || 'AI 建议的改法' }))
                    setAdvice(null); setError(null)
                  }}
                  onDropFilled={pid => setAiFilled(m => {
                    const n = { ...m }; delete n[pid]; return n
                  })} />
        )}
        {stage === 3 && <Stage3 trace={trace} onChoose={onChoose} running={running}
                                onAlign={health?.ai_bound ? align : undefined}
                                aligning={busy === 'align'}
                                onBack={() => setStage(2)} onNext={() => setStage(4)} />}
        {stage === 4 && <Stage4 trace={trace} onBack={() => setStage(3)} onNext={() => setStage(5)}
                                onFix={() => setStage(2)} />}
        {stage === 5 && (
          <Stage5 trace={trace} material={material} guidedSid={guidedSid}
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
      {wf.provenance === 'user_guided' && (
        <div className="mb-4">
          <Alert tone="warn" title="这份选型流程是在线引导下组装的，尚未核验">
            依据由你确认、整套公式由你过目确认，数值全部由你填写或由引擎算出——
            <b>AI 没有提供任何数值</b>。
            <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
              但这<b>不等于经过核验</b>：取证只证明那份文件里确实有这个标准号，
              不证明这个公式适用于你的工况。
              引导模型：<span className="num">{wf.generated_by || '未知'}</span>。
              正式定稿前请对照依据原文复核。
            </div>
          </Alert>
        </div>
      )}
      {wf.provenance === 'ai_generated' && (
        <div className="mb-4">
          <Alert tone="err" title="这是旧版「AI 一次性起草」留下的流程，没有任何依据">
            它没有经过依据检索与取证，也没有人核对过公式——那一版的门槛太低，
            已经不再提供。
            <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
              建议回首页用<b>引导式选型</b>重做一遍：那条路会先检索并取证依据，
              再让你确认整套公式。<b>不要拿这份结果定稿。</b>
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
              {wf.provenance === 'builtin' ? '' : '用户目录/'}workflows/{wf.material}.yaml
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

function Stage2({ wf, values, setValues, error, running, onRun, aiBound, aiFilled,
                 busy, assist, advice, reply, setReply, onFill, onAskFix,
                 onApplyFix, onDropFilled }: {
  wf: Workflow
  values: Record<string, string>
  setValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  error: ApiError | null
  running: boolean
  onRun: () => void
  aiBound: boolean
  /** id → 这个值是怎么来的。有它就在字段上打标，不能让 AI 补的和手输的看起来一样 */
  aiFilled: Record<string, string>
  busy: string
  assist: FillResult | null
  advice: FixAdvice | null
  reply: string
  setReply: (v: string) => void
  onFill: (reply?: string) => void
  onAskFix: (param: string) => void
  onApplyFix: (param: string, value: number | string) => void
  onDropFilled: (param: string) => void
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
            <Alert tone="err" title="参数有问题">
              {error.message}
              {aiBound && error.param && !advice && (
                <div className="mt-2">
                  <button className="btn text-[12px] py-1 px-2.5"
                          disabled={busy === 'fix'}
                          onClick={() => onAskFix(error.param as string)}>
                    {busy === 'fix' ? '想办法中…' : '问问 AI 该怎么改 →'}
                  </button>
                </div>
              )}
            </Alert>
          </div>
        )}

        {advice && (
          <div className="mb-4">
            <Alert tone="info" title={'关于「' + advice.name_zh + '」'}>
              {advice.explain}
              {advice.suggestion !== null && (
                <div className="mt-2">
                  建议改成 <b className="num">{String(advice.suggestion)}</b>
                  {advice.unit ? ' ' + advice.unit : ''}
                  {advice.how && (
                    <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>
                      怎么来的：{advice.how}
                    </div>
                  )}
                  <button className="btn btn-p text-[12px] py-1 px-2.5 mt-2"
                          onClick={() => onApplyFix(advice.param,
                                                    advice.suggestion as number | string)}>
                    采用这个值
                  </button>
                </div>
              )}
              {advice.rejected && (
                <div className="mt-2 text-[11px]" style={{ color: '#fbbf24' }}>
                  它原本还给了一个建议值，但那个值自己也没过校验，已丢弃：{advice.rejected}
                </div>
              )}
              {advice.ask && (
                <div className="mt-2 text-[12px]">它想问你：{advice.ask}</div>
              )}
            </Alert>
          </div>
        )}

        {missing > 0 && aiBound && (
          <div className="mb-4">
            <Alert tone="info" title={'还差 ' + missing + ' 项必填参数'}>
              可以直接点「开始计算」——缺项会被引擎挡下；
              也可以让 AI 把<b>能负责任地补的</b>补上、<b>不敢猜的问你</b>。
              <div className="mt-2">
                <button className="btn text-[12px] py-1 px-2.5" disabled={busy === 'fill'}
                        onClick={() => onFill()}>
                  {busy === 'fill' ? '补齐中…' : '让 AI 补齐这 ' + missing + ' 项 →'}
                </button>
              </div>
              <div className="mt-1.5 text-[11px]" style={{ color: 'var(--sub)' }}>
                补进来的值会走<b>与你手输完全相同</b>的校验通道，并在字段上标出来；
                结果与导出报告里也会逐项列明哪几个不是你填的。
              </div>
            </Alert>
          </div>
        )}

        {assist && (assist.questions.length > 0
                    || Object.keys(assist.rejected).length > 0) && (
          <div className="mb-4">
            <Alert tone="warn" title={assist.questions.length
              ? '有几项它不敢替你猜' : '有几项没能补上'}>
              {assist.questions.length > 0 && (
                <>
                  <ul className="ml-4 list-disc text-[12px]">
                    {assist.questions.map(q => (
                      <li key={q.id}>{q.ask}
                        <span style={{ color: 'var(--sub)' }}>（{q.why}）</span>
                      </li>
                    ))}
                  </ul>
                  <div className="flex gap-2 mt-2">
                    <input className="inp flex-1 text-[12px]" value={reply}
                           placeholder="一句话回答上面的问题，它会据此再补一轮"
                           onChange={e => setReply(e.target.value)}
                           onKeyDown={e => {
                             if (e.key === 'Enter' && reply.trim()) onFill(reply)
                           }} />
                    <button className="btn" disabled={busy === 'fill' || !reply.trim()}
                            onClick={() => onFill(reply)}>回答</button>
                  </div>
                </>
              )}
              {Object.entries(assist.rejected).map(([pid, why]) => (
                <div key={pid} className="text-[11px] mt-1.5" style={{ color: '#fbbf24' }}>
                  <span className="num">{pid}</span>：{why}
                </div>
              ))}
            </Alert>
          </div>
        )}

        <div className="text-[12px] font-medium mb-2" style={{ color: '#93c5fd' }}>必需参数</div>
        <div className="grid md:grid-cols-2 gap-3 mb-5">
          {required.map(i => (
            <InputRow key={i.id} def={i} value={values[i.id] ?? ''}
                      onChange={v => { set(i.id, v); onDropFilled(i.id) }}
                      invalid={error?.param === i.id} filledBy={aiFilled[i.id]} />
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
                <InputRow key={i.id} def={i} value={values[i.id] ?? ''}
                          onChange={v => { set(i.id, v); onDropFilled(i.id) }}
                          invalid={error?.param === i.id} filledBy={aiFilled[i.id]} />
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

function InputRow({ def, value, onChange, invalid, filledBy }: {
  def: InputDef; value: string; onChange: (v: string) => void; invalid?: boolean
  /** 非空 = 这个值是 AI 补的，内容是它给的理由。**不能和手输的看起来一样。** */
  filledBy?: string
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
        {filledBy && (
          <span className="badge b-warn ml-1.5" title={'AI 补的：' + filledBy}
                style={{ fontSize: 10 }}>AI 补的</span>
        )}
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
      {filledBy && (
        <div className="text-[11px] mt-1" style={{ color: '#fbbf24' }}>
          AI 补的：{filledBy}
          <span style={{ color: 'var(--sub)' }}>　改一下就恢复成你自己的值</span>
        </div>
      )}
      {hint && <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>{hint}</div>}
    </div>
  )
}

// ── 阶段 3：分步计算 ───────────────────────────────────────────────

function Stage3({ trace, onChoose, running, onAlign, aligning, onBack, onNext }: {
  trace: Trace | null
  onChoose: (s: string, v: string) => void
  running: boolean
  onAlign?: (stepId: string, label: string, given: string,
             candidates: Candidate[]) => void
  aligning?: boolean
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
        <CalcCard key={s.id} step={s} index={idx + 1} onChoose={onChoose}
                  onAlign={onAlign} aligning={aligning} />
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

function Stage5({ trace, material, guidedSid, values, choices, onBack, onNext }: {
  trace: Trace | null
  material: string
  guidedSid?: string | null
  values: Record<string, unknown>
  choices: Record<string, string>
  onBack: () => void
  onNext: () => void
}) {
  const [exporting, setExporting] = useState('')
  const [exportErr, setExportErr] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAs, setSavedAs] = useState('')
  const [saveErr, setSaveErr] = useState('')

  // 导出接口收的是物料 id，服务端自己重跑一遍引擎再渲染——
  // 引导出来的规格在保存之前还不在磁盘上，那条路走不通。如实说明，不给假按钮。
  const canExport = !guidedSid || !!savedAs

  const download = async (fmt: 'pdf' | 'xlsx') => {
    setExporting(fmt); setExportErr('')
    try {
      const { blob, filename } = await api.exportReport(
        fmt, { material: savedAs || material, values, choices })
      saveBlob(blob, filename)
    } catch (e) {
      setExportErr((e as RequestError).message)
    } finally { setExporting('') }
  }

  const save = async () => {
    if (!guidedSid) return
    setSaving(true); setSaveErr('')
    try {
      const out = await api.guidedSave(guidedSid)
      setSavedAs(out.material)
    } catch (e) {
      setSaveErr((e as RequestError).message)
    } finally { setSaving(false) }
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
                 <button className="btn" disabled={!!exporting || !canExport}
                         title={canExport ? '' : '先保存这个物料，导出接口才能按 id 重跑一遍引擎'}
                         onClick={() => void download('pdf')}>
                   {exporting === 'pdf' ? '生成中…' : '导出 PDF'}
                 </button>
                 <button className="btn" disabled={!!exporting || !canExport}
                         title={canExport ? '' : '先保存这个物料，导出接口才能按 id 重跑一遍引擎'}
                         onClick={() => void download('xlsx')}>
                   {exporting === 'xlsx' ? '生成中…' : '导出 Excel'}
                 </button>
               </div>
             }>
      {exportErr && <div className="mb-3"><Alert tone="err" title="导出失败">{exportErr}</Alert></div>}
      {guidedSid && (
        <div className="mb-4">
          {savedAs ? (
            <Alert tone="ok" title="已保存到用户目录">
              下次<b>离线也能选</b>「{trace?.name_zh || savedAs}」了。
              它在物料列表里会标成 🔴 未经核验——那是准确的现状：
              依据是真的、可点开的，但没有人拿标准原件逐格核对过。
            </Alert>
          ) : (
            <Alert tone="info" title="跑通了，要把这个物料留下来吗？">
              保存之后它会进入你的物料列表，<b>下次离线也能选</b>；
              流程与依据一并存进用户目录，不会混进随包数据。
              <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
                刻意不自动保存：一份没跑通的流程存下来，
                只会在物料列表里留一个点进去就报错的入口。
              </div>
              <button className="btn btn-p mt-3" disabled={saving}
                      onClick={() => void save()}>
                {saving ? '保存中…' : '保存这个物料 →'}
              </button>
            </Alert>
          )}
          {saveErr && <div className="mt-2"><Alert tone="err" title="保存失败">{saveErr}</Alert></div>}
        </div>
      )}
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

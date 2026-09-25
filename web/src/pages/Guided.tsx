import { useCallback, useEffect, useState } from 'react'
import { api, RequestError } from '../api'
import { Alert, Badge, Section, Spinner } from '../components/ui'
import type {
  AiDraft, BasisCandidate, Evidence, GuidedInput, GuidedSession, RepairAttempt,
} from '../types'

/**
 * 引导式选型 —— SKILL.md 阶段 0~3/4。
 *
 * 这一页只做一件事：**把一份可执行的选型流程组装出来**，
 * 每一段都由用户拍板。组装完之后交给工作台，按普通物料那样走完阶段 2~6。
 *
 * 界面上的按钮置灰只是提示，**闸门在后端**：依据没确认就调阶段 2 的接口，
 * 服务端会直接 409。把闸门放在前端等于没有闸门。
 */

const EVIDENCE: Record<Evidence['status'], {
  icon: string; label: string; kind: 'ok' | 'warn' | 'err' | 'info'; note: string
}> = {
  cross_checked: {
    icon: '✓✓', label: '两处互证', kind: 'ok',
    note: '两个不同来源的正文里都出现了这个标准号',
  },
  trusted: {
    icon: '✓', label: '可信站点', kind: 'ok',
    note: '只有一处，但那一处是你指定的可信站点（mechtool.cn）',
  },
  single_source: {
    icon: '✓', label: '仅一处', kind: 'warn',
    note: '只有一个来源印证到，可以用，但请自己再核一遍',
  },
  unverified: {
    icon: '✗', label: '取证不通过', kind: 'err',
    note: '服务器抓到了它引用的页面，正文里却没有它声称的标准号——不能用',
  },
  unverifiable_claim: {
    icon: '?', label: '无法自动取证', kind: 'info',
    note: '这条表述里提不出可比对的标准号，只能由你自己核对',
  },
}

export default function Guided({ materialText, onReady, onCancel }: {
  materialText: string
  /** 整套公式确认之后，带着会话 id 进工作台 */
  onReady: (sessionId: string, material: string) => void
  onCancel: () => void
}) {
  const [sess, setSess] = useState<GuidedSession | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState<{ message: string; reasons?: string[] } | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [custom, setCustom] = useState('')
  const [draft, setDraft] = useState<AiDraft | null>(null)

  const act = useCallback(async (what: string, fn: () => Promise<GuidedSession>) => {
    setBusy(what); setErr(null)
    try {
      setSess(await fn())
    } catch (e) {
      const ex = e as RequestError & { detail?: { reasons?: string[] } }
      setErr({ message: ex.message, reasons: ex.detail?.reasons })
    } finally { setBusy('') }
  }, [])

  /** 兜底档。**它不改变会话状态**：有草案不等于流程走通了。 */
  const drawDraft = useCallback(async () => {
    if (!sess) return
    setBusy('ai-draft')
    try {
      setDraft((await api.guidedAiDraft(sess.id)).draft)
    } catch (e) {
      setErr({ message: (e as RequestError).message })
    } finally { setBusy('') }
  }, [sess])

  // 开会话。**此时还没有检索，也还没花一个 token。**
  useEffect(() => {
    let alive = true
    setBusy('start'); setErr(null)
    api.guidedStart(materialText)
      .then(s => { if (alive) setSess(s) })
      .catch((e: RequestError) => { if (alive) setErr({ message: e.message }) })
      .finally(() => { if (alive) setBusy('') })
    return () => { alive = false }
  }, [materialText])

  if (busy === 'start') return <Spinner label={`正在为「${materialText}」开一个引导会话…`} />
  if (!sess) {
    return (
      <Section title="引导式选型" sub={materialText}>
        {err && <Alert tone="err" title="开不了引导会话">{err.message}</Alert>}
        <div className="mt-3"><button className="btn" onClick={onCancel}>← 回首页</button></div>
      </Section>
    )
  }

  const basisDone = !!sess.basis?.confirmed_at

  return (
    <div className="max-w-[900px] mx-auto">
      <Section
        title={`引导式选型 · ${sess.name_zh || materialText}`}
        sub="按 SKILL.md 的阶段走：先定依据，再定参数，最后由你确认整套公式"
        right={<button className="btn" onClick={onCancel}>← 放弃</button>}>

        {err && (
          <div className="mb-4">
            <Alert tone="err" title="这一步没能完成">
              {err.message}
              {err.reasons?.length ? (
                <ul className="mt-2 ml-4 list-disc text-[11px]">
                  {err.reasons.slice(0, 6).map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              ) : null}
              <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
                引擎已经把这些原因退回给模型让它改过了，仍然没过。
                你可以再试一次、换一个模型、自己填依据往下走，
                或者用下面的兜底档。
              </div>
              <div className="mt-3">
                <button className="btn" disabled={!!busy}
                        onClick={() => void drawDraft()}>
                  {busy === 'ai-draft' ? '生成中…' : '让 AI 直接给一份参考草案 →'}
                </button>
                <div className="mt-1.5 text-[11px]" style={{ color: 'var(--sub)' }}>
                  它会把整个选型做完、<b>包括出数</b>，但那些数<b>没有任何出处</b>：
                  不经过引擎、存不成物料、也进不了选型报告。
                </div>
              </div>
            </Alert>
          </div>
        )}

        {draft && <DraftPanel draft={draft} />}

        {/* ── 阶段 1：依据检索 ── */}
        <StageCard n={1} title="依据检索" active={!basisDone}
                   done={basisDone}
                   sub="AI 只提候选，服务器自己去抓页面核对，最后由你拍板">
          {sess.candidates.length === 0 ? (
            <>
              <div className="text-[12px] mb-3" style={{ color: 'var(--sub)' }}>
                这一步会真的联网检索，并把每一条候选依据引用的页面抓回来，
                核对正文里是否<b>真的出现了它声称的标准号</b>。对不上的选不了。
                <div className="text-[11px] mt-1">
                  检索按可用性降级：绑了搜索服务就用它；没绑但这家 AI 自己会上网
                  （如 DeepSeek 的 Anthropic 兼容端点）就用那个，
                  <b>那条路会计入你自己账号的费用</b>；都没有就退到白名单站内目录。
                </div>
              </div>
              <button className="btn btn-p" disabled={!!busy}
                      onClick={() => void act('research', () => api.guidedResearch(sess.id))}>
                {busy === 'research' ? '检索并取证中…' : '开始检索依据 →'}
              </button>
            </>
          ) : (
            <>
              <SearchRung search={sess.search} />
              <Repair log={sess.repair_log.basis} what="候选依据" />
              <div className="grid gap-3 mt-3">
                {sess.candidates.map(c => (
                  <BasisCard key={c.id} c={c} chosen={sess.basis?.id === c.id}
                             busy={!!busy}
                             onChoose={() => void act('basis',
                               () => api.guidedChooseBasis(sess.id, c.id))} />
                ))}
              </div>
              <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--line)' }}>
                <div className="text-[12px] mb-2" style={{ color: 'var(--sub)' }}>
                  都不合适？自己填一条依据（引擎不去核你自己的话，但会如实记下这是你填的）
                </div>
                <div className="flex gap-2">
                  <input className="inp flex-1" value={custom}
                         placeholder="例：GB/T 1095-2003《普通型 平键》表 1；或 手上的纸质《机械设计手册》第3卷"
                         onChange={e => setCustom(e.target.value)} />
                  <button className="btn" disabled={!!busy || custom.trim().length < 4}
                          onClick={() => void act('basis', () => api.guidedChooseBasis(
                            sess.id, '', { claim: custom.trim() }))}>
                    用这条
                  </button>
                </div>
              </div>
            </>
          )}
          {basisDone && (
            <div className="mt-3 text-[12px]">
              已确认依据：<b>{sess.basis.claim}</b>{' '}
              <Badge kind={EVIDENCE[sess.basis.status as Evidence['status']]?.kind ?? 'info'}>
                {sess.basis.status === 'self_declared' ? '你自己填的'
                  : EVIDENCE[sess.basis.status as Evidence['status']]?.label ?? sess.basis.status}
              </Badge>
            </div>
          )}
        </StageCard>

        {/* ── 阶段 2：参数清单 ── */}
        <StageCard n={2} title="参数引导" active={basisDone && !sess.inputs_confirmed_at}
                   done={!!sess.inputs_confirmed_at}
                   locked={!basisDone} lockNote="先在阶段 1 确认一条依据"
                   sub="按这条依据要问你哪些工况；手册里查的量会写明去哪查">
          {sess.inputs.length === 0 ? (
            <button className="btn btn-p" disabled={!!busy || !sess.can_propose_inputs}
                    onClick={() => void act('inputs', () => api.guidedProposeInputs(sess.id))}>
              {busy === 'inputs' ? '整理参数清单中…' : '列出要问的参数 →'}
            </button>
          ) : (
            <>
              <Repair log={sess.repair_log.inputs} what="参数清单" />
              <InputList inputs={sess.inputs} />
              {!sess.inputs_confirmed_at && (
                <div className="flex gap-2 mt-3">
                  <button className="btn" disabled={!!busy}
                          onClick={() => void act('inputs',
                            () => api.guidedProposeInputs(sess.id))}>
                    重新列一份
                  </button>
                  <div className="flex-1" />
                  <button className="btn btn-p" disabled={!!busy}
                          onClick={() => void act('confirm-inputs',
                            () => api.guidedConfirmInputs(sess.id))}>
                    确认这份清单 →
                  </button>
                </div>
              )}
            </>
          )}
        </StageCard>

        {/* ── 阶段 3/4：整套计算与校核 ── */}
        <StageCard n={3} title="计算与校核" active={!!sess.inputs_confirmed_at}
                   done={!!sess.formulas_confirmed_at}
                   locked={!sess.inputs_confirmed_at} lockNote="先确认参数清单"
                   sub="整套步骤一次性过目：公式、代号、出自依据哪一节">
          {sess.steps.length === 0 ? (
            <button className="btn btn-p" disabled={!!busy || !sess.can_propose_steps}
                    onClick={() => void act('steps', () => api.guidedProposeSteps(sess.id))}>
              {busy === 'steps' ? '编排计算步骤中…' : '给出整套计算与校核 →'}
            </button>
          ) : (
            <>
              <Repair log={sess.repair_log.steps} what="计算步骤" />
              <StepTable steps={sess.steps} />
              {sess.missing_inputs.length > 0 && (
                <div className="mt-3">
                  <Alert tone="warn" title="它说还缺几个量">
                    <ul className="ml-4 list-disc text-[12px]">
                      {sess.missing_inputs.map(m => (
                        <li key={m.id}>
                          <b>{m.name_zh}</b>（{m.id}）—— {m.why}；{m.where}
                        </li>
                      ))}
                    </ul>
                  </Alert>
                </div>
              )}
              {sess.notes.length > 0 && (
                <div className="mt-3 text-[11px]" style={{ color: 'var(--sub)' }}>
                  {sess.notes.map((n, i) => <div key={i}>· {n}</div>)}
                </div>
              )}

              {!sess.formulas_confirmed_at ? (
                <div className="mt-4 p-3 rounded-lg"
                     style={{ background: 'rgba(245,158,11,.08)',
                              border: '1px solid rgba(245,158,11,.35)' }}>
                  <label className="flex items-start gap-2 text-[13px] cursor-pointer">
                    <input type="checkbox" className="mt-0.5" checked={confirmed}
                           onChange={e => setConfirmed(e.target.checked)} />
                    <span>
                      我已对照<b>{sess.basis.claim}</b>核对以上全部公式
                      <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>
                        这一步不是走过场：引擎只做算术，
                        <b>公式对不对只有你能判断</b>。这句确认会原样进入结果警告与导出报告。
                      </div>
                    </span>
                  </label>
                  <div className="flex gap-2 mt-3">
                    <button className="btn" disabled={!!busy}
                            onClick={() => void act('steps',
                              () => api.guidedProposeSteps(sess.id))}>
                      重新编排
                    </button>
                    <div className="flex-1" />
                    <button className="btn btn-p" disabled={!!busy || !confirmed}
                            onClick={() => void act('confirm-steps',
                              () => api.guidedConfirmFormulas(sess.id, true))}>
                      确认，去填参数 →
                    </button>
                  </div>
                </div>
              ) : (
                <div className="mt-4">
                  <Alert tone="ok" title="流程已组装完成">
                    接下来与随包物料完全一样：填参数 → 引擎计算 → 校核 → 结果 → 采购链接。
                    <div className="mt-3">
                      <button className="btn btn-p"
                              onClick={() => onReady(sess.id, sess.material)}>
                        进入工作台 →
                      </button>
                    </div>
                  </Alert>
                </div>
              )}
            </>
          )}
        </StageCard>
      </Section>
    </div>
  )
}

// ── 小件 ──────────────────────────────────────────────────────────

function StageCard({ n, title, sub, active, done, locked, lockNote, children }: {
  n: number; title: string; sub?: string
  active?: boolean; done?: boolean; locked?: boolean; lockNote?: string
  children: React.ReactNode
}) {
  return (
    <div className={`card p-4 mb-4 ${locked ? 'opacity-50' : ''}`}
         style={active ? { borderColor: 'var(--brand)' } : undefined}>
      <div className="flex items-center gap-2 mb-1">
        <span className="badge">{done ? '✓' : n}</span>
        <span className="text-[14px] font-medium">阶段 {n} · {title}</span>
      </div>
      {sub && <div className="text-[11px] mb-3" style={{ color: 'var(--sub)' }}>{sub}</div>}
      {locked
        ? <div className="text-[12px]" style={{ color: 'var(--sub)' }}>{lockNote}</div>
        : children}
    </div>
  )
}

/** 这次检索走的是哪条路。用户有权知道它是搜索服务查的还是站内目录翻的。 */
function SearchRung({ search }: { search: GuidedSession['search'] }) {
  const label: Record<string, string> = {
    binding: '你绑定的搜索服务',
    provider: 'AI 服务商自带的联网',
    whitelist: '白名单站内目录（没绑搜索服务）',
    none: '三条路都没有结果',
  }
  return (
    <div className="text-[11px] mb-2" style={{ color: 'var(--sub)' }}>
      检索途径：{label[search.rung] ?? search.rung}
      {search.detail ? ` · ${search.detail}` : ''}
      {search.problems?.length ? (
        <span style={{ color: '#fbbf24' }}>　（{search.problems.join('；')}）</span>
      ) : null}
    </div>
  )
}

/** 模型修了几轮才过闸门。**不藏。** */
function Repair({ log, what }: { log?: RepairAttempt[]; what: string }) {
  if (!log || log.length <= 1) return null
  const passed = log[log.length - 1]?.passed
  return (
    <div className="text-[11px] mb-2" style={{ color: passed ? 'var(--sub)' : '#fbbf24' }}>
      {passed
        ? `这份${what}第 1 次没过合规检查，引擎把原因退回去让模型改，第 ${log.length} 次通过。`
        : `这份${what}连着 ${log.length} 次都没过合规检查。`}
    </div>
  )
}

function BasisCard({ c, chosen, busy, onChoose }: {
  c: BasisCandidate; chosen: boolean; busy: boolean; onChoose: () => void
}) {
  const ev = EVIDENCE[c.evidence.status] ?? EVIDENCE.unverified
  return (
    <div className="card p-3" style={chosen ? { borderColor: 'var(--brand)' } : undefined}>
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="text-[13px] font-medium">{c.claim}</div>
        <Badge kind={ev.kind} title={ev.note}>{ev.icon} {ev.label}</Badge>
      </div>
      {c.why && <div className="text-[12px] mb-2" style={{ color: 'var(--sub)' }}>{c.why}</div>}

      <div className="text-[11px] mb-2" style={{ color: 'var(--sub)' }}>{ev.note}</div>

      <ul className="text-[11px] ml-4 list-disc mb-2" style={{ color: 'var(--sub)' }}>
        {c.outline.map((s, i) => <li key={i}>{s}</li>)}
      </ul>

      <div className="text-[11px]">
        {c.evidence.hits.map(h => (
          <div key={h.url}>
            <a href={h.final_url || h.url} target="_blank" rel="noreferrer"
               className="underline" style={{ color: '#60a5fa' }}>{h.title || h.url}</a>
            <span style={{ color: 'var(--sub)' }}>　{h.domain}</span>
          </div>
        ))}
        {c.evidence.misses.map(h => (
          <div key={h.url} style={{ color: '#fbbf24' }}>
            {h.url}　抓到了，但正文里没有这个标准号
          </div>
        ))}
        {c.evidence.failures.map(h => (
          <div key={h.url} style={{ color: 'var(--sub)' }}>{h.url}　{h.error}</div>
        ))}
        {c.dropped_urls.length > 0 && (
          <div style={{ color: '#f87171' }}>
            已剔除 {c.dropped_urls.length} 条不在检索结果里的引用
          </div>
        )}
      </div>

      <div className="mt-3">
        <button className="btn btn-p text-[12px] py-1 px-2.5"
                disabled={busy || !c.evidence.usable}
                title={c.evidence.usable ? '' : '取证不通过的依据不能用'}
                onClick={onChoose}>
          {chosen ? '已选用' : '用这一条'}
        </button>
      </div>
    </div>
  )
}

function InputList({ inputs }: { inputs: GuidedInput[] }) {
  const rounds = [...new Set(inputs.map(i => i.round || 1))].sort((a, b) => a - b)
  return (
    <div className="overflow-x-auto scroll">
      <table className="tbl">
        <thead>
          <tr><th>轮次</th><th>参数</th><th>符号</th><th>单位</th><th>范围</th><th>去哪查 / 说明</th></tr>
        </thead>
        <tbody>
          {rounds.flatMap(r => inputs.filter(i => (i.round || 1) === r).map(i => (
            <tr key={i.id}>
              <td className="num">{r}</td>
              <td>{i.name_zh}{i.from_handbook && <Badge kind="warn">查手册</Badge>}</td>
              <td className="num">{i.id}</td>
              <td>{i.unit || '—'}</td>
              <td className="num">
                {i.domain?.min !== undefined ? `${i.domain.min} ~ ${i.domain.max}` : '—'}
              </td>
              <td className="text-[12px]" style={{ color: 'var(--sub)' }}>{i.hint || '—'}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  )
}

function StepTable({ steps }: { steps: Record<string, unknown>[] }) {
  return (
    <div className="overflow-x-auto scroll">
      <table className="tbl">
        <thead>
          <tr><th>序号</th><th>步骤</th><th>类型</th><th>公式 / 判据</th><th>出自依据</th></tr>
        </thead>
        <tbody>
          {steps.map((s, i) => {
            const kind = String(s.kind ?? '')
            const formula = kind === 'check'
              ? `${s.value} ${s.op} ${s.limit}`
              : String(s.expr ?? s.template ?? s.fallback ?? '—')
            const ref = String((s.source as { ref?: string } | undefined)?.ref ?? '—')
            return (
              <tr key={String(s.id ?? i)}>
                <td className="num">{i + 1}</td>
                <td>{String(s.name_zh ?? s.id ?? '')}</td>
                <td className="text-[12px]" style={{ color: 'var(--sub)' }}>{kind}</td>
                <td className="num text-[12px]">{formula}</td>
                <td className="text-[12px]" style={{ color: 'var(--sub)' }}>{ref}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 兜底档的呈现。**这一段的设计目标是让人不会把它当成选型结果。**
 *
 * 所以：红色边框、标题直说"不是选型结果"、每一行都标引擎复核的结论、
 * 复制出去的文本自带免责头。
 */
function DraftPanel({ draft }: { draft: AiDraft }) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(draft.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch { /* 剪贴板没权限就算了，文本本来就显示在下面 */ }
  }

  const ARITH: Record<string, { label: string; kind: 'ok' | 'err' | 'info' }> = {
    ok: { label: '算术✓', kind: 'ok' },
    mismatch: { label: '算术✗', kind: 'err' },
    unreadable: { label: '未核', kind: 'info' },
  }

  return (
    <div className="card p-4 mb-4"
         style={{ borderColor: 'rgba(239,68,68,.45)',
                  background: 'rgba(239,68,68,.04)' }}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <div className="text-[14px] font-medium" style={{ color: '#f87171' }}>
            ⚠ AI 参考草案 —— 这不是选型结果
          </div>
          <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>
            没有经过确定性引擎，里面<b>每一个数都没有可追溯的出处</b>；
            它没有存进你的物料库，也不会出现在选型报告里。
          </div>
        </div>
        <button className="btn text-[12px] py-1 px-2.5 whitespace-nowrap"
                onClick={() => void copy()}>
          {copied ? '已复制' : '复制全文'}
        </button>
      </div>

      <div className="text-[11px] mb-3 p-2 rounded"
           style={{ background: 'var(--card2, rgba(255,255,255,.03))',
                    color: 'var(--sub)' }}>
        <b>引擎复核了什么：</b>只重算了 AI 自己写的代入式——
        {draft.arith.ok} 步对得上，
        <b style={{ color: draft.arith.mismatch ? '#f87171' : 'inherit' }}>
          {draft.arith.mismatch} 步对不上
        </b>
        ，{draft.arith.unreadable} 步没法核。
        <br />
        <b>公式是否适用于你的工况、系数取值对不对：引擎没有、也无法判断。</b>
      </div>

      {!draft.parsed ? (
        <pre className="text-[11px] whitespace-pre-wrap scroll"
             style={{ maxHeight: 420, overflow: 'auto' }}>{draft.text}</pre>
      ) : (
        <>
          {draft.given.length > 0 && (
            <DraftTable title="已知条件" head={['项目', '符号', '值', '单位', '说明']}
                        rows={draft.given.map(g => [g.label, g.symbol,
                          `${g.value} ${g.unit || ''}`.trim(), g.unit, g.note])} />
          )}

          <div className="text-[12px] font-medium mt-3 mb-1">计算过程</div>
          <div className="overflow-x-auto scroll">
            <table className="tbl">
              <thead>
                <tr><th>项目</th><th>公式</th><th>代入</th><th>结果</th>
                    <th>AI 自述出处</th><th>引擎复核</th></tr>
              </thead>
              <tbody>
                {draft.steps.map((s, i) => {
                  const a = ARITH[s.arith] ?? ARITH.unreadable
                  return (
                    <tr key={i}>
                      <td>{s.label}</td>
                      <td className="num text-[12px]">{s.formula}</td>
                      <td className="num text-[12px]">{s.substitution}</td>
                      <td className="num">{s.value}{s.unit ? ` ${s.unit}` : ''}</td>
                      <td className="text-[11px]" style={{ color: 'var(--sub)' }}>
                        {s.source || '未说明'}
                      </td>
                      <td>
                        <Badge kind={a.kind}
                               title={s.arith === 'mismatch'
                                 ? `按它自己的代入式算是 ${s.arith_value}`
                                 : ''}>
                          {a.label}
                        </Badge>
                        {s.arith === 'mismatch' && (
                          <div className="text-[11px]" style={{ color: '#f87171' }}>
                            实为 {s.arith_value}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {draft.checks.length > 0 && (
            <DraftTable title="校核" head={['项目', '判据', '代入', '结论', 'AI 自述出处']}
                        rows={draft.checks.map(c => [c.label, c.criterion,
                          c.substitution, c.passed ? '通过' : '不通过',
                          c.source || '未说明'])} />
          )}
          {draft.result.length > 0 && (
            <DraftTable title="选型结果（未经引擎计算）" head={['项目', '值']}
                        rows={draft.result.map(r => [r.label,
                          `${r.value} ${r.unit || ''}`.trim()])} />
          )}
          {draft.caveats.length > 0 && (
            <div className="mt-3 text-[11px]" style={{ color: 'var(--sub)' }}>
              <b>AI 自己说的不可靠之处：</b>
              <ul className="ml-4 list-disc mt-1">
                {draft.caveats.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function DraftTable({ title, head, rows }: {
  title: string; head: string[]; rows: (string | undefined)[][]
}) {
  return (
    <>
      <div className="text-[12px] font-medium mt-3 mb-1">{title}</div>
      <div className="overflow-x-auto scroll">
        <table className="tbl">
          <thead><tr>{head.map(h => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j}>{c || '—'}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

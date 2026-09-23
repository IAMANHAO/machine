import { useState } from 'react'
import { api, RequestError } from '../api'
import type { Health, Source, Step, SuggestResult, Trace } from '../types'
import { Alert, Badge, ConfidenceBadge } from './ui'

interface Props {
  sources: Source[]
  trace: Trace | null
  health: Health | null
  material: string
  values: Record<string, string>
  stage: number
  mode: string
  onApplySuggestions: (v: Record<string, string>) => void
  onGoSettings: () => void
}

/** 右侧面板：AI 助手（三个窄接口）与 依据/缓存。 */
export default function SidePanel(props: Props) {
  const [tab, setTab] = useState<'ai' | 'src'>('src')
  const used = new Set((props.trace?.sources ?? []).map(s => s.table_file))
  const list = props.trace?.sources?.length ? props.trace.sources : props.sources

  return (
    <aside className="right card flex flex-col sticky top-[72px] h-[calc(100vh-96px)]">
      <div className="flex border-b" style={{ borderColor: 'var(--line)' }}>
        <button className={`tab ${tab === 'ai' ? 'on' : ''}`} onClick={() => setTab('ai')}>
          AI 助手
        </button>
        <button className={`tab ${tab === 'src' ? 'on' : ''}`} onClick={() => setTab('src')}>
          依据 / 缓存
        </button>
      </div>
      {tab === 'ai' ? <AiTab {...props} /> : <SourceTab list={list} used={used} hasTrace={!!props.trace} />}
    </aside>
  )
}

// ── AI ──────────────────────────────────────────────────────────────

function AiTab({ health, material, values, stage, mode, trace,
                onApplySuggestions, onGoSettings }: Props) {
  const bound = !!health?.ai_bound
  const [sug, setSug] = useState<SuggestResult | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [explain, setExplain] = useState<{ step: string; text: string } | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const known = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''))

  const runSuggest = async () => {
    setBusy('suggest'); setErr(''); setExplain(null)
    try {
      const r = await api.suggest(material, known, mode)
      setSug(r)
      setPicked(new Set(Object.keys(r.suggestions)))
    } catch (e) { setErr((e as RequestError).message) } finally { setBusy('') }
  }

  const runExplain = async (step: Step) => {
    setBusy(`explain:${step.id}`); setErr(''); setSug(null)
    try {
      const r = await api.explain(material, step, mode)
      setExplain({ step: step.name_zh, text: r.text })
    } catch (e) { setErr((e as RequestError).message) } finally { setBusy('') }
  }

  const apply = () => {
    if (!sug) return
    const patch: Record<string, string> = {}
    for (const id of picked) {
      const s = sug.suggestions[id]
      if (s) patch[id] = String(s.value)
    }
    onApplySuggestions(patch)
    setSug(null)
  }

  const explainable = (trace?.steps ?? []).filter(
    s => s.status === 'ok' && s.kind !== 'check' && s.value !== null)

  return (
    <div className="flex-1 overflow-y-auto scroll p-3">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Badge kind={bound ? 'ok' : 'warn'}>{bound ? '已绑定账号' : '未绑定'}</Badge>
        {bound && health?.mode && (
          <span className="text-[11px]" style={{ color: 'var(--sub)' }}>
            {health.mode === 'online' ? '在线' : '离线'}
          </span>
        )}
      </div>

      {!bound && (
        <div className="mb-3">
          <Alert tone="info" title="AI 是可选增强，不是运行前提">
            <p>本软件不自带 API key，AI 由你绑定自己的 DeepSeek 账号提供，费用计入你的账号。</p>
            <p className="mt-2">
              <b style={{ color: 'var(--txt)' }}>未绑定也能用</b>：
              计算、校核、出表、采购链接完全不受影响。
              参数建议会退化成只给规格里写明的典型值——本工作流目前没写，
              所以离线时这一项给不出东西，需要你自己填。
            </p>
          </Alert>
          <button className="btn btn-p w-full mt-3" onClick={onGoSettings}>去设置页绑定 →</button>
        </div>
      )}

      {err && <div className="mb-3"><Alert tone="err" title="调用失败">{err}</Alert></div>}

      {/* 阶段 2：参数建议 */}
      {stage <= 2 && (
        <div className="mb-3">
          <button className="btn w-full" disabled={busy === 'suggest'} onClick={() => void runSuggest()}>
            {busy === 'suggest' ? '思考中…' : '为缺失参数给建议'}
          </button>
          {sug && <SuggestionList sug={sug} picked={picked} setPicked={setPicked} onApply={apply} />}
        </div>
      )}

      {/* 阶段 3+：解释某一步 */}
      {stage >= 3 && explainable.length > 0 && (
        <div className="mb-3">
          <div className="text-[12px] mb-2" style={{ color: 'var(--sub)' }}>
            {bound ? '挑一步让 AI 用白话讲讲' : '解释功能需要绑定账号'}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {explainable.slice(0, 12).map(s => (
              <button key={s.id} className="btn text-[11px] py-1 px-2" disabled={!bound || !!busy}
                      onClick={() => void runExplain(s)}>
                {busy === `explain:${s.id}` ? '…' : s.name_zh}
              </button>
            ))}
          </div>
          {explain && (
            <div className="msg msg-ai mt-3">
              <div className="text-[11px] mb-1.5" style={{ color: 'var(--sub)' }}>{explain.step}</div>
              {explain.text}
            </div>
          )}
        </div>
      )}

      <div className="text-[11px] pt-3 mt-3 border-t space-y-1.5"
           style={{ borderColor: 'var(--line)', color: 'var(--sub)' }}>
        <div style={{ color: 'var(--txt)' }}>AI 在本流程中只做三件事</div>
        <div>① 把整句工况解析成物料与参数</div>
        <div>② 为缺失参数给建议值与理由（需你逐项确认）</div>
        <div>③ 用白话解释某一步已经算完的结果</div>
        <div className="pt-1">
          三件事都不碰数值计算。建议值会走与手工输入完全相同的校验通道，
          过不了的会被引擎当场挡下。
        </div>
      </div>
    </div>
  )
}

function SuggestionList({ sug, picked, setPicked, onApply }: {
  sug: SuggestResult
  picked: Set<string>
  setPicked: (s: Set<string>) => void
  onApply: () => void
}) {
  const ids = Object.keys(sug.suggestions)
  const rejected = Object.entries(sug.rejected)
  const skipped = Object.entries(sug.skipped)

  const toggle = (id: string) => {
    const next = new Set(picked)
    next.has(id) ? next.delete(id) : next.add(id)
    setPicked(next)
  }

  return (
    <div className="mt-3">
      <div className="text-[11px] mb-2" style={{ color: 'var(--sub)' }}>
        {sug.source === 'offline' ? '离线：取自规格里标注的典型值' : 'AI 建议'}
        {sug.usage && <> · {sug.usage.total_tokens} tokens</>}
      </div>

      {ids.length === 0 && skipped.length === 0 && (
        <div className="text-[12px]" style={{ color: 'var(--sub)' }}>参数已经齐了，没有要建议的。</div>
      )}

      {ids.length === 0 && skipped.length > 0 && sug.source === 'offline' && (
        <Alert tone="warn" title="离线模式下不替你猜工况参数">
          还缺 {skipped.length} 项：<span className="num">{skipped.map(([id]) => id).join('、')}</span>。
          这些值直接决定工况系数与带型选择，猜错会一路错到底，所以规格里没写明推荐值的
          一律不给建议。请按实际工况填写；绑定账号后 AI 可以结合你的描述给出带理由的建议。
        </Alert>
      )}

      {ids.map(id => {
        const s = sug.suggestions[id]
        return (
          <label key={id} className="card2 p-2.5 mb-2 block cursor-pointer">
            <div className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={picked.has(id)}
                     onChange={() => toggle(id)} />
              <div className="min-w-0 flex-1">
                <div className="text-[12px] flex justify-between gap-2">
                  <span>{s.name_zh}</span>
                  <span className="num" style={{ color: '#34d399' }}>
                    {String(s.value)}{s.unit ? ` ${s.unit}` : ''}
                  </span>
                </div>
                {s.rationale && (
                  <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>{s.rationale}</div>
                )}
              </div>
            </div>
          </label>
        )
      })}

      {skipped.length > 0 && ids.length > 0 && (
        <div className="text-[11px] mb-2 space-y-1" style={{ color: 'var(--sub)' }}>
          <div>以下几项需要你自己定：</div>
          {skipped.map(([id, why]) => (
            <div key={id}><span className="num">{id}</span>：{String(why)}</div>
          ))}
        </div>
      )}

      {rejected.length > 0 && (
        <div className="mb-2">
          <Alert tone="err" title="以下建议被引擎挡下了">
            {rejected.map(([id, why]) => (
              <div key={id} className="mt-1"><span className="num">{id}</span>：{why}</div>
            ))}
          </Alert>
        </div>
      )}

      {ids.length > 0 && (
        <button className="btn btn-p w-full" disabled={picked.size === 0} onClick={onApply}>
          采用选中的 {picked.size} 项
        </button>
      )}
      {sug.note && ids.length > 0 && (
        <div className="text-[11px] mt-2" style={{ color: 'var(--sub)' }}>{sug.note}</div>
      )}
    </div>
  )
}

// ── 依据 / 缓存 ──────────────────────────────────────────────────────

function SourceTab({ list, used, hasTrace }: {
  list: Source[]; used: Set<string>; hasTrace: boolean
}) {
  const unverified = list.filter(s => s.confidence !== 'verified').length

  return (
    <div className="flex-1 overflow-y-auto scroll p-3">
      <div className="text-[12px] mb-3" style={{ color: 'var(--sub)' }}>
        {hasTrace ? '本次选型引用的数据表 · 全部来自本地缓存' : '该物料的本地缓存 · 离线可用'}
      </div>

      {list.map(s => (
        <div key={s.table_file} className="card2 p-3 mb-2">
          <div className="flex items-center justify-between mb-1 gap-2">
            <span className="num text-[12px] break-all">{s.table_file}</span>
            <ConfidenceBadge level={s.confidence} compact />
          </div>
          <div className="text-[11px]" style={{ color: 'var(--sub)' }}>{s.data_source || '—'}</div>
          {s.second_source && (
            <div className="text-[11px] mt-1" style={{ color: '#34d399' }}>
              第二信源：{s.second_source}
            </div>
          )}
          {s.last_verified && (
            <div className="text-[11px] mt-1 num" style={{ color: 'var(--sub)' }}>
              核验于 {String(s.last_verified)}
            </div>
          )}
          {hasTrace && used.has(s.table_file) && (
            <div className="text-[11px] mt-1" style={{ color: '#60a5fa' }}>本次已引用</div>
          )}
        </div>
      ))}

      {list.length === 0 && (
        <div className="text-[12px] py-4" style={{ color: 'var(--sub)' }}>该物料暂无缓存数据表。</div>
      )}

      {unverified > 0 && (
        <div className="text-[11px] p-3 rounded-lg mt-2"
             style={{ background: 'rgba(245,158,11,.08)', border: '1px solid rgba(245,158,11,.3)', color: '#fbbf24' }}>
          ⚠ 其中 {unverified} 张尚未完成双源交叉核验。
          计算与校核脚本正常运行，但结果仅供初步设计参考。
        </div>
      )}
    </div>
  )
}

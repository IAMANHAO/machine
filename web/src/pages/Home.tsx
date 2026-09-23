import { useEffect, useState } from 'react'
import { api, RequestError } from '../api'
import type { IntentResult, Material, Project } from '../types'
import { Alert, ConfidenceBadge, Empty, Spinner, STATUS } from '../components/ui'

const STATUS_BADGE: Record<Material['status'], { label: string; kind: 'ok' | 'warn' | 'info' }> = {
  ready: { label: '已支持', kind: 'ok' },
  cache_only: { label: '待规格化', kind: 'warn' },
  planned: { label: '待生成', kind: 'warn' },
}

/** AI 起草的物料单独打标 —— 它没有任何信源，不该和随包的看起来一样。 */
function badgeFor(m: Material) {
  return m.provenance === 'ai_generated'
    ? { label: 'AI 起草', kind: 'warn' as const }
    : STATUS_BADGE[m.status]
}

export default function Home({ onStart, onOpen, aiBound }: {
  onStart: (material: string, values?: Record<string, unknown>) => void
  onOpen: (project: Project) => void
  aiBound: boolean
}) {
  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [intent, setIntent] = useState<IntentResult | null>(null)
  const [intentErr, setIntentErr] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [draftErr, setDraftErr] = useState<string[]>([])
  const [materials, setMaterials] = useState<Material[] | null>(null)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    Promise.all([api.materials(), api.projects()])
      .then(([m, p]) => { setMaterials(m); setProjects(p) })
      .catch(e => setError(e.message))
  }, [])

  /**
   * 知识库里没有这个物料时的出路：让 AI 起草一份流程。
   *
   * 起草出来的是**草稿**，直接带进工作台跑。跑通之后用户才决定存不存——
   * 一份没跑通的流程存下来，只会在物料列表里留一个点进去就报错的入口。
   */
  const draft = async (name: string) => {
    setDrafting(true); setDraftErr([])
    try {
      const d = await api.draftWorkflow(name)
      // 先存再进工作台：引擎要能按 id 加载到它，才跑得起来
      await api.saveDraft(d.spec)
      const fresh = await api.materials()
      setMaterials(fresh)
      onStart(d.material, intent?.values)
    } catch (e) {
      const err = e as RequestError & { detail?: { reasons?: string[] } }
      setDraftErr(err.detail?.reasons?.length ? err.detail.reasons : [err.message])
    } finally { setDrafting(false) }
  }

  const parse = async () => {
    if (!text.trim()) return
    setParsing(true); setIntentErr(''); setDraftErr([]); setIntent(null)
    try {
      const r = await api.intent(text)
      setIntent(r)
      // 认出物料就直接带着已识别的参数进工作台
      if (r.material) onStart(r.material, r.values)
    } catch (e) {
      setIntentErr((e as RequestError).message)
    } finally { setParsing(false) }
  }

  const ready = materials?.filter(m => m.status === 'ready') ?? []
  const cacheOnly = materials?.filter(m => m.status === 'cache_only') ?? []
  const planned = materials?.filter(m => m.status === 'planned') ?? []

  return (
    <>
      <div className="card p-5 mb-5">
        <div className="text-[13px] mb-2" style={{ color: 'var(--sub)' }}>
          选择物料进入选型流程。计算全部在本地完成，离线可用。
        </div>
        <div className="flex gap-2 flex-col md:flex-row">
          <input className="inp flex-1" value={text}
                 placeholder="例：帮我选一根同步带，电机功率 5.5 kW，转速 1450 r/min，中心距 400 mm"
                 onChange={e => setText(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') void parse() }} />
          <button className="btn btn-p whitespace-nowrap"
                  disabled={!text.trim() || parsing} onClick={() => void parse()}>
            {parsing ? '识别中…' : '开始选型 →'}
          </button>
        </div>
        <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
          {aiBound
            ? <>在线：<span className="kbd">AI 解析整句工况</span>，识别结果仍需你在阶段 2 确认</>
            : <>离线：<span className="kbd">关键词匹配</span>，只认物料名与带单位的数值；绑定账号后可解析整句（设置页）</>}
        </div>
        {intentErr && <div className="mt-3"><Alert tone="err" title="识别失败">{intentErr}</Alert></div>}
        {intent && !intent.material && (
          <div className="mt-3">
            <Alert tone="warn" title={intent.can_draft
              ? `知识库里还没有「${intent.unknown_material}」`
              : '没认出是哪种物料'}>
              {intent.can_draft ? (
                <>
                  可以让 AI 起草一份这个物料的选型流程，再按正常流程走下去。
                  <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
                    起草的是<b>流程</b>，不是数据：所有需要查手册的系数都会做成输入项，
                    由你自己填。结果会标成 <b>🔴 未经核验</b>——
                    公式是否适用于你的工况，需要你对照手册确认。
                  </div>
                  <button className="btn btn-p mt-3" disabled={drafting}
                          onClick={() => void draft(intent.unknown_material)}>
                    {drafting ? '起草中…' : `让 AI 起草「${intent.unknown_material}」的选型流程 →`}
                  </button>
                </>
              ) : (intent.notes || '请直接从下面的物料入口里选一个。')}
            </Alert>
          </div>
        )}
        {draftErr.length > 0 && (
          <div className="mt-3">
            <Alert tone="err" title="起草的流程没通过合规检查">
              引擎宁可拒绝，也不让一份带着编造系数的流程跑起来。可以再试一次。
              <ul className="mt-2 ml-4 list-disc text-[11px]">
                {draftErr.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </Alert>
          </div>
        )}
      </div>

      {error && <div className="card p-4 mb-5 text-[13px]" style={{ color: '#f87171' }}>{error}</div>}
      {!materials && !error && <Spinner label="正在读取物料目录…" />}

      {materials && (
        <>
          <div className="mb-2 text-[13px] font-medium">快捷物料入口</div>
          <div className="grid grid-cols-3 md:grid-cols-6 lg:grid-cols-12 gap-2 mb-6">
            {[...ready, ...cacheOnly, ...planned].map(m => {
              const badge = badgeFor(m)
              const usable = m.status === 'ready'
              return (
                <button key={m.id}
                        onClick={() => usable ? onStart(m.id) : undefined}
                        disabled={!usable}
                        title={m.provenance === 'ai_generated'
                          ? `${m.name_zh} · AI 起草，未经核验`
                          : (usable ? `${m.name_zh} · ${m.standard}` : m.note)}
                        className={`card p-3 text-center transition ${usable ? 'hover:border-blue-500' : 'opacity-60 cursor-not-allowed'}`}>
                  <div className="text-[22px] mb-1">{m.icon}</div>
                  <div className="text-[12px] truncate" title={m.name_zh}>{shortName(m.name_zh)}</div>
                  <div className={`badge b-${badge.kind} mt-1`} style={{ fontSize: 10 }}>{badge.label}</div>
                </button>
              )
            })}
          </div>
        </>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <div className="mb-2 text-[13px] font-medium">最近项目</div>
          {!projects && !error && <Spinner />}
          {projects?.length === 0 && <Empty>还没有选型记录。选一个物料开始吧。</Empty>}
          {projects?.map(p => {
            const s = STATUS[p.status]
            return (
              <button key={p.id} onClick={() => onOpen(p)}
                      className="card p-4 mb-3 w-full text-left hover:border-blue-500 transition">
                <div className="flex items-center justify-between mb-2 gap-2">
                  <div className="font-medium text-[14px] truncate">{p.title}</div>
                  <span className={`badge ${s.cls} whitespace-nowrap`}>{s.icon} {s.label}</span>
                </div>
                <div className="text-[12px] num" style={{ color: 'var(--sub)' }}>{p.summary || '—'}</div>
                {p.headline && (
                  <div className="text-[11px] mt-2 truncate" style={{ color: 'var(--sub)' }}>
                    {p.headline} · {p.updated_at.slice(0, 10)}
                  </div>
                )}
              </button>
            )
          })}
        </div>

        <div>
          <div className="mb-2 text-[13px] font-medium">缓存与信源状态</div>
          <div className="card p-4">
            {materials?.filter(m => m.table_count > 0).map(m => (
              <div key={m.id} className="flex justify-between items-center text-[13px] mb-3 gap-2">
                <span className="truncate">{shortName(m.name_zh)}数据表</span>
                <span className="flex items-center gap-1.5 whitespace-nowrap">
                  <ConfidenceBadge level={m.confidence} compact />
                  <span className="text-[12px]" style={{ color: 'var(--sub)' }}>{m.table_count} 张</span>
                </span>
              </div>
            ))}
            <div className="text-[11px] pt-3 border-t" style={{ borderColor: 'var(--line)', color: '#fbbf24' }}>
              ⚠ 目前全部数据表均为单一信源，尚未完成双源核验。
            </div>
          </div>

          {planned.length > 0 && (
            <div className="card p-4 mt-4">
              <div className="text-[13px] font-medium mb-2">待生成工作流</div>
              <div className="text-[12px]" style={{ color: 'var(--sub)' }}>
                {planned.map(p => p.name_zh).join('、')} 等 {planned.length} 种物料尚未建立可执行工作流。
                新增一个物料 = 写一份 YAML 规格，不需要改代码。
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

/** "同步带（梯形齿）" -> "同步带"，卡片放不下括号里的限定语。 */
function shortName(name: string): string {
  return name.replace(/（.*?）/g, '').trim() || name
}

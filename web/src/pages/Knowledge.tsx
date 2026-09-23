import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, RequestError } from '../api'
import { Alert, Badge, ConfidenceBadge, Empty, Spinner } from '../components/ui'
import VerifyDialog from '../components/VerifyDialog'
import PointsDialog from '../components/PointsDialog'
import type { AuditAll, Gap, MaterialAudit, ProbeStatus, TableAudit } from '../types'

type Tab = 'tables' | 'gaps' | 'reach'

const PROBE_LOOK: Record<ProbeStatus, { icon: string; label: string; color: string }> = {
  ok: { icon: '✅', label: '可选出', color: '#34d399' },
  check_failed: { icon: '⚠', label: '校核不通过', color: '#fbbf24' },
  no_solution: { icon: '🔧', label: '系列内无解', color: '#fbbf24' },
  data_missing: { icon: '⛔', label: '数据缺失', color: '#f87171' },
  needs_choice: { icon: '🤔', label: '需决策', color: '#60a5fa' },
  input_error: { icon: '·', label: '参数不合法', color: '#8ba0b3' },
  error: { icon: '·', label: '错误', color: '#8ba0b3' },
}

const GAP_LABEL: Record<Gap['kind'], string> = {
  frontmatter: '信源元数据不全',
  fake_green: '假绿灯',
  grid_hole: '表格空洞',
  empty_group: '缺整个分组',
  unreachable: '工况算不出来',
  no_solution: '该规格系列内无解',
}

export default function Knowledge() {
  const [audit, setAudit] = useState<AuditAll | null>(null)
  const [error, setError] = useState('')
  const [material, setMaterial] = useState<string>('')
  const [tab, setTab] = useState<Tab>('gaps')
  const [verifying, setVerifying] = useState<TableAudit | null>(null)
  const [filling, setFilling] = useState<Gap | null>(null)
  const [toast, setToast] = useState('')

  const load = useCallback(() => {
    setError('')
    api.audit(true)
      .then(a => {
        setAudit(a)
        setMaterial(m => m
          || a.materials.find(x => x.has_workflow)?.material
          || a.materials[0]?.material || '')
      })
      .catch((e: RequestError) => setError(e.message))
  }, [])

  useEffect(load, [load])

  const current: MaterialAudit | undefined = useMemo(
    () => audit?.materials.find(m => m.material === material),
    [audit, material])

  const afterEdit = useCallback((msg: string) => {
    setVerifying(null); setFilling(null); setToast(msg)
    load()
    window.setTimeout(() => setToast(''), 4000)
  }, [load])

  if (error) return <Alert tone="err" title="读不到知识库">{error}</Alert>
  if (!audit) return <Spinner label="正在体检数据表与工况可达性…" />

  const t = audit.totals
  return (
    <>
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <div>
          <div className="text-[15px] font-semibold">知识库 / 缓存管理</div>
          <div className="text-[12px] mt-0.5" style={{ color: 'var(--sub)' }}>
            数据自检、缺口补录与双源核验
          </div>
        </div>
        <button className="btn" onClick={load}>重新体检</button>
      </div>

      {toast && <div className="mb-4"><Alert tone="info" title="已保存">{toast}</Alert></div>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Stat label="数据表" value={`${t.tables}`} hint={`具备核验条件 ${t.can_be_verified}`} />
        <Stat label="已双源核验" value={`${t.verified} / ${t.tables}`}
              tone={t.verified === t.tables ? 'ok' : 'warn'} />
        <Stat label="阻断缺口" value={`${t.blocking}`} hint={`另有警告 ${t.warning}`}
              tone={t.blocking ? 'err' : 'ok'} />
        <Stat label="工况可达" value={`${t.reachable} / ${t.probed}`}
              hint="代表性工况能算到底的比例"
              tone={t.reachable === t.probed ? 'ok' : 'warn'} />
      </div>

      {t.verified === 0 && (
        <div className="mb-5">
          <Alert tone="warn" title="目前没有任何一张表完成双源核验">
            按 <span className="num">references/source_priority.md</span> 的规则，关键工况系数需
            「≥ 第 2 级信源 + 至少 2 个独立信源」才能标为 🟢。在此之前，所有选型结果只能作为
            初步设计参考。核验时必须填写第二信源——这是为了让绿灯可追溯，而不是走个形式。
          </Alert>
        </div>
      )}

      <div className="flex gap-2 mb-4 flex-wrap">
        {audit.materials.map(m => (
          <button key={m.material} className="btn"
                  style={{
                    borderColor: material === m.material ? 'var(--brand)' : 'var(--line)',
                    color: material === m.material ? '#93c5fd' : 'var(--txt)',
                  }}
                  onClick={() => setMaterial(m.material)}>
            {m.material}
            <span className="ml-1.5 text-[11px]" style={{ color: 'var(--sub)' }}>
              {m.counts.tables} 表 · {m.has_workflow ? `${m.counts.blocking} 阻断` : '未规格化'}
            </span>
          </button>
        ))}
      </div>

      {!current ? <Empty>选一个物料查看详情。</Empty> : (
        <>
          <div className="flex border-b mb-4" style={{ borderColor: 'var(--line)' }}>
            <button className={`tab ${tab === 'gaps' ? 'on' : ''}`} onClick={() => setTab('gaps')}>
              缺口清单（{current.gaps.length}）
            </button>
            <button className={`tab ${tab === 'tables' ? 'on' : ''}`} onClick={() => setTab('tables')}>
              数据表（{current.tables.length}）
            </button>
            <button className={`tab ${tab === 'reach' ? 'on' : ''}`} onClick={() => setTab('reach')}>
              工况可达性
            </button>
          </div>

          {tab === 'gaps' && <GapList audit={current} onFill={setFilling} />}
          {tab === 'tables' && <TableList audit={current} onVerify={setVerifying}
                                          onChanged={afterEdit} />}
          {tab === 'reach' && <ReachMatrix audit={current} />}
        </>
      )}

      {verifying && (
        <VerifyDialog table={verifying} onClose={() => setVerifying(null)}
                      onDone={afterEdit} />
      )}
      {filling && current && (
        <PointsDialog material={current.material} gap={filling}
                      onClose={() => setFilling(null)} onDone={afterEdit} />
      )}
    </>
  )
}

function Stat({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: 'ok' | 'warn' | 'err'
}) {
  const color = tone === 'ok' ? '#34d399' : tone === 'warn' ? '#fbbf24'
              : tone === 'err' ? '#f87171' : 'var(--txt)'
  return (
    <div className="card p-4">
      <div className="text-[11px] mb-1" style={{ color: 'var(--sub)' }}>{label}</div>
      <div className="text-[20px] num font-semibold" style={{ color }}>{value}</div>
      {hint && <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>{hint}</div>}
    </div>
  )
}

// ── 缺口清单 ────────────────────────────────────────────────────────

function GapList({ audit, onFill }: { audit: MaterialAudit; onFill: (g: Gap) => void }) {
  if (!audit.has_workflow) {
    return (
      <Alert tone="warn" title="这个物料还没做过覆盖度检查">
        它只有 .md 人读文档，还没有可执行的 YAML 规格，因此无法做网格覆盖分析与工况可达性探测。
        上面「缺口 0」的意思是<b style={{ color: 'var(--txt)' }}>没检查</b>，不是没问题——
        规格化之后才能知道这些表够不够用。
      </Alert>
    )
  }
  if (audit.gaps.length === 0) {
    return <Empty>没有发现缺口：信源元数据齐备，探测网格上的工况都能算到底。</Empty>
  }
  const blocking = audit.gaps.filter(g => g.severity === 'blocking')
  const warning = audit.gaps.filter(g => g.severity !== 'blocking')

  return (
    <>
      <div className="mb-4">
        <Alert tone="info" title="缺口是怎么算出来的">
          引擎按工作流的探测网格真跑了 {audit.probe.total} 个代表性工况，
          再结合数据表的网格覆盖分析得出。<b style={{ color: 'var(--txt)' }}>「数据缺失」</b>要去补表，
          <b style={{ color: 'var(--txt)' }}>「系列内无解」</b>是选型结论、要去改设计——两者指引相反，不要混淆。
        </Alert>
      </div>

      {blocking.length > 0 && <GapGroup title="阻断" gaps={blocking} tone="err" onFill={onFill} />}
      {warning.length > 0 && <GapGroup title="警告" gaps={warning} tone="warn" onFill={onFill} />}
    </>
  )
}

function GapGroup({ title, gaps, tone, onFill }: {
  title: string; gaps: Gap[]; tone: 'err' | 'warn'; onFill: (g: Gap) => void
}) {
  const border = tone === 'err' ? 'rgba(239,68,68,.35)' : 'rgba(245,158,11,.35)'
  return (
    <div className="mb-5">
      <div className="text-[13px] font-medium mb-2">
        {title} <span style={{ color: 'var(--sub)' }}>（{gaps.length}）</span>
      </div>
      {gaps.map((g, i) => {
        const fillable = g.kind === 'grid_hole' || g.kind === 'unreachable'
        return (
          <div key={i} className="card p-4 mb-2" style={{ borderColor: border }}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <Badge kind={tone}>{GAP_LABEL[g.kind] ?? g.kind}</Badge>
                  {g.table && <span className="num text-[11px]" style={{ color: 'var(--sub)' }}>{g.table}</span>}
                </div>
                <div className="text-[13px]">{g.message}</div>
                {g.fix_hint && (
                  <div className="text-[12px] mt-1.5" style={{ color: '#fbbf24' }}>→ {g.fix_hint}</div>
                )}
              </div>
              {fillable && (
                <button className="btn whitespace-nowrap" onClick={() => onFill(g)}>去补录</button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── 数据表 ──────────────────────────────────────────────────────────

function TableList({ audit, onVerify, onChanged }: {
  audit: MaterialAudit
  onVerify: (t: TableAudit) => void
  onChanged: (msg: string) => void
}) {
  const [busy, setBusy] = useState('')

  const unverify = async (t: TableAudit) => {
    setBusy(t.name)
    try {
      await api.unverifyTable(t.material, t.name, '人工退回，需重新核对')
      onChanged(`${t.file} 已退回 single_source`)
    } catch (e) {
      onChanged(`退回失败：${(e as RequestError).message}`)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>数据表</th><th>主信源</th><th>第二信源</th>
              <th>状态</th><th>最近核验</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {audit.tables.map(t => (
              <tr key={t.name}>
                <td className="num align-top">{t.file}</td>
                <td className="align-top" style={{ maxWidth: 260 }}>{t.data_source || '—'}</td>
                <td className="align-top" style={{ maxWidth: 220 }}>
                  {t.second_source || <span style={{ color: 'var(--sub)' }}>未填</span>}
                </td>
                <td className="align-top">
                  <ConfidenceBadge level={t.confidence} />
                  {t.issues.map((iss, i) => (
                    <div key={i} className="text-[11px] mt-1" style={{ color: '#f87171' }}>⚠ {iss}</div>
                  ))}
                  {t.todo && (
                    <div className="text-[11px] mt-1" style={{ color: 'var(--sub)' }}>待办：{t.todo}</div>
                  )}
                </td>
                <td className="num align-top">{t.last_verified ?? '—'}</td>
                <td className="align-top">
                  {t.confidence === 'verified' ? (
                    <button className="btn text-[11px] py-1 px-2" disabled={busy === t.name}
                            onClick={() => void unverify(t)}>退回</button>
                  ) : (
                    <button className="btn text-[11px] py-1 px-2"
                            onClick={() => onVerify(t)}>核验</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── 可达性矩阵 ──────────────────────────────────────────────────────

function ReachMatrix({ audit }: { audit: MaterialAudit }) {
  const matrix = audit.probe.matrix
  if (!matrix.length) {
    return <Empty>该物料的工作流还没有定义探测网格（workflows 里的 probe 段）。</Empty>
  }

  const keys = Object.keys(matrix[0].combo)
  const [rowKey, colKey] = keys.length >= 2 ? keys : [keys[0], null]
  const rows = [...new Set(matrix.map(m => String(m.combo[rowKey])))]
  const cols = colKey ? [...new Set(matrix.map(m => String(m.combo[colKey])))] : ['']
  const at = (r: string, c: string) => matrix.find(
    m => String(m.combo[rowKey]) === r && (!colKey || String(m.combo[colKey]) === c))

  return (
    <>
      <div className="mb-4">
        <Alert tone="info" title={`这个软件现在到底能选什么：${audit.probe.reachable} / ${audit.probe.total}`}>
          下面每一格都是引擎真跑出来的结果，不是估计。⛔ 的格子说明该工况现在算不出来，
          点缺口清单里的「去补录」可以直接补对应的数据。
        </Alert>
      </div>

      <div className="card p-4 overflow-x-auto scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>{rowKey} \ {colKey ?? ''}</th>
              {cols.map(c => <th key={c} className="num">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r}>
                <td className="num font-medium">{r}</td>
                {cols.map(c => {
                  const cell = at(r, c)
                  const look = cell ? PROBE_LOOK[cell.status] : null
                  return (
                    <td key={c} title={cell?.message || look?.label}>
                      {look ? (
                        <span style={{ color: look.color }}>
                          {look.icon} <span className="text-[11px]">{look.label}</span>
                        </span>
                      ) : '—'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex gap-4 flex-wrap mt-3 text-[11px]" style={{ color: 'var(--sub)' }}>
        {(['ok', 'check_failed', 'no_solution', 'data_missing'] as ProbeStatus[]).map(s => (
          <span key={s}>
            <span style={{ color: PROBE_LOOK[s].color }}>{PROBE_LOOK[s].icon}</span> {PROBE_LOOK[s].label}
          </span>
        ))}
      </div>
    </>
  )
}

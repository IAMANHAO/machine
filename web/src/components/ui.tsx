import type { ReactNode } from 'react'
import type { Confidence, RunStatus, StepStatus } from '../types'

/** 置信度是读自数据表真实 frontmatter 的事实，不是装饰。 */
export const CONFIDENCE: Record<Confidence, { icon: string; label: string; cls: string }> = {
  verified: { icon: '🟢', label: '已双源核验', cls: 'b-ok' },
  single_source: { icon: '🟡', label: '单一信源', cls: 'b-warn' },
  self_defined: { icon: '🟡', label: '内部整理', cls: 'b-warn' },
  unknown: { icon: '🔴', label: '来源不明', cls: 'b-err' },
}

export const STATUS: Record<StepStatus, { icon: string; label: string; cls: string }> = {
  ok: { icon: '✅', label: '通过', cls: 'b-ok' },
  check_failed: { icon: '❌', label: '校核不通过', cls: 'b-err' },
  data_missing: { icon: '⛔', label: '数据缺失', cls: 'b-err' },
  // 与数据缺失区分：这是引擎给出的选型结论，不是知识库的问题
  no_solution: { icon: '🔧', label: '该系列内无解', cls: 'b-warn' },
  needs_choice: { icon: '🤔', label: '需要决策', cls: 'b-warn' },
  skipped: { icon: '·', label: '未执行', cls: '' },
  // 「本条分支用不到」≠「出错所以没跑到」，两者混同会让人以为流程坏了
  not_applicable: { icon: '–', label: '本分支不适用', cls: '' },
}

export function Badge({ kind, children, title }: {
  kind?: 'ok' | 'warn' | 'err' | 'info'
  children: ReactNode
  title?: string
}) {
  return <span className={`badge ${kind ? `b-${kind}` : ''}`} title={title}>{children}</span>
}

export function ConfidenceBadge({ level, compact }: { level: Confidence; compact?: boolean }) {
  const c = CONFIDENCE[level] ?? CONFIDENCE.unknown
  return (
    <span className={`badge ${c.cls}`} title={`数据核验状态：${c.label}`}>
      {c.icon}{compact ? '' : ` ${c.label}`}
    </span>
  )
}

export function StatusBadge({ status }: { status: StepStatus }) {
  const s = STATUS[status] ?? STATUS.skipped
  return <span className={`badge ${s.cls}`}>{s.icon} {s.label}</span>
}

export function Section({ title, right, children, sub }: {
  title: string; sub?: string; right?: ReactNode; children: ReactNode
}) {
  return (
    <div>
      <div className="flex items-end justify-between mb-3 gap-3">
        <div>
          <div className="text-[15px] font-semibold">{title}</div>
          {sub && <div className="text-[12px] mt-0.5" style={{ color: 'var(--sub)' }}>{sub}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="card p-8 text-center text-[13px]" style={{ color: 'var(--sub)' }}>
      {children}
    </div>
  )
}

export function Spinner({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-[13px] py-6" style={{ color: 'var(--sub)' }}>
      <span className="inline-block w-3 h-3 rounded-full border-2 border-t-transparent animate-spin"
            style={{ borderColor: 'var(--brand)', borderTopColor: 'transparent' }} />
      {label}
    </div>
  )
}

/** 数据缺失 / 需要决策 / 出错时的统一呈现：说清楚发生了什么、缺口在哪。 */
export function Alert({ tone, title, children }: {
  tone: 'err' | 'warn' | 'info' | 'ok'
  title: string
  children?: ReactNode
}) {
  const bg = {
    err: 'rgba(239,68,68,.08)', warn: 'rgba(245,158,11,.08)',
    info: 'rgba(59,130,246,.08)', ok: 'rgba(34,197,94,.08)',
  }[tone]
  const border = {
    err: 'rgba(239,68,68,.35)', warn: 'rgba(245,158,11,.35)',
    info: 'rgba(59,130,246,.35)', ok: 'rgba(34,197,94,.35)',
  }[tone]
  const color = { err: '#f87171', warn: '#fbbf24', info: '#60a5fa', ok: '#4ade80' }[tone]
  return (
    <div className="p-4 rounded-lg text-[13px] leading-relaxed"
         style={{ background: bg, border: `1px solid ${border}` }}>
      <div className="font-medium mb-1" style={{ color }}>{title}</div>
      {children && <div style={{ color: 'var(--sub)' }}>{children}</div>}
    </div>
  )
}

export function resultTone(status: RunStatus): 'ok' | 'warn' | 'err' {
  if (status === 'ok') return 'ok'
  if (status === 'needs_choice' || status === 'no_solution') return 'warn'
  return 'err'
}

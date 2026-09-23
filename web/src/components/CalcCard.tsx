import type { Candidate, Step } from '../types'
import { Alert, ConfidenceBadge, StatusBadge } from './ui'

/**
 * 阶段 3 的计算卡片。四要素缺一不可：公式、代入、结果、依据。
 * 这正是"每一次选型都能被逐行验证"落到界面上的样子。
 */
export default function CalcCard({ step, index, onChoose }: {
  step: Step
  index: number
  onChoose?: (stepId: string, value: string) => void
}) {
  // 未执行与本分支不适用都不画卡片：前者会在阶段 3 底部统一说明，
  // 后者根本不属于这条流程
  if (step.status === 'skipped' || step.status === 'not_applicable') return null

  const conf = step.source?.confidence
  const blocked = step.status === 'data_missing' || step.status === 'needs_choice'
                  || step.status === 'no_solution'

  return (
    <div className="card p-4 mb-3" style={blocked ? { borderColor: 'rgba(239,68,68,.4)' } : undefined}>
      <div className="flex items-start justify-between mb-3 gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="badge b-info">第 {index} 步</span>
          <span className="font-medium text-[14px]">{step.name_zh}</span>
        </div>
        {blocked ? <StatusBadge status={step.status} />
                 : conf ? <ConfidenceBadge level={conf} /> : null}
      </div>

      {!blocked && (
        <div className="grid md:grid-cols-3 gap-3 text-[13px]">
          <Cell label="公式">{step.formula || '—'}</Cell>
          <Cell label="代入">{step.substitution || '—'}</Cell>
          <Cell label="结果">
            <b style={{ color: '#34d399' }}>
              {step.value_display || String(step.value ?? '—')}
              {step.unit ? ` ${step.unit}` : ''}
            </b>
          </Cell>
        </div>
      )}

      {!blocked && step.source?.ref && (
        <div className="mt-3 pt-3 border-t text-[11px]" style={{ borderColor: 'var(--line)', color: 'var(--sub)' }}>
          依据：{step.source.ref}
          {step.source.table_file && <> · <span className="num">{step.source.table_file}</span></>}
          {step.source.last_verified && <> · 核验于 {String(step.source.last_verified)}</>}
        </div>
      )}

      {step.detail?.series_range && (
        <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>
          标准系列共 {step.detail.series_size} 档，范围{' '}
          <span className="num">{step.detail.series_range[0]} ~ {step.detail.series_range[1]}</span>
        </div>
      )}

      {step.detail?.by === 'engine_default' && (
        <div className="mt-2 text-[11px]" style={{ color: '#fbbf24' }}>
          该值未由用户指定，按工作流的推荐式取值
        </div>
      )}

      {step.status === 'data_missing' && step.error && (
        <Alert tone="err" title="该工况点没有数据，无法计算">
          <p>{step.error.message}</p>
          {step.error.gap && <p className="mt-2">缺口：{step.error.gap}</p>}
          <p className="mt-2 text-[12px]">
            引擎不会用外推值把这一步糊过去。请在知识库页补齐该数据表，或改用有数据覆盖的工况。
          </p>
        </Alert>
      )}

      {step.status === 'no_solution' && step.error && (
        <Alert tone="warn" title="这个规格系列内无解">
          <p>{step.error.message}</p>
          {step.error.remedy && (
            <p className="mt-2" style={{ color: '#fbbf24' }}>→ {step.error.remedy}</p>
          )}
          <p className="mt-2 text-[12px]">
            这不是数据缺失——所需值已经算出来了，只是超出了该规格系列的范围。
            要解决它请回去调整设计（换更大节距 / 降功率 / 改传动比），补知识库没有用。
          </p>
        </Alert>
      )}

      {step.status === 'needs_choice' && step.error && (
        <ChoicePicker stepId={step.error.step || step.id} err={step.error} onChoose={onChoose} />
      )}

      {step.note && !blocked && (
        <div className="mt-2 text-[11px]" style={{ color: 'var(--sub)' }}>说明：{step.note}</div>
      )}
    </div>
  )
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ color: 'var(--sub)' }} className="text-[11px] mb-1">{label}</div>
      <div className="num break-words">{children}</div>
    </div>
  )
}

/** select 步骤：引擎给候选与理由，决策权留给人（M4 起 AI 可给推荐）。 */
function ChoicePicker({ stepId, err, onChoose }: {
  stepId: string
  err: { reason?: string | null; message: string; candidates?: Candidate[] }
  onChoose?: (stepId: string, value: string) => void
}) {
  return (
    <div className="mt-1">
      <Alert tone="warn" title="这一步需要你来决定">
        {err.reason || err.message}
      </Alert>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2 mt-3">
        {(err.candidates || []).map(c => (
          <button key={c.value} className="card2 p-3 text-left hover:border-blue-500 transition"
                  onClick={() => onChoose?.(stepId, c.value)}>
            <div className="flex items-center justify-between mb-1">
              <span className="num font-medium text-[13px]">{c.value}</span>
              <span className="text-[11px]" style={{ color: 'var(--brand)' }}>选它 →</span>
            </div>
            <div className="text-[12px]">{c.label}</div>
            {c.detail && (
              <div className="text-[11px] mt-1.5 space-y-0.5" style={{ color: 'var(--sub)' }}>
                {Object.entries(c.detail).map(([k, v]) => (
                  <div key={k}><span className="num">{k}</span>：{String(v)}</div>
                ))}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

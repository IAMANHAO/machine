import type { Procure, Trace, Workflow } from '../types'

export const STAGES = [
  { n: 0, label: '物料识别' },
  { n: 1, label: '依据检索' },
  { n: 2, label: '参数引导' },
  { n: 3, label: '分步计算' },
  { n: 4, label: '校核' },
  { n: 5, label: '结果输出' },
  { n: 6, label: '采购链接' },
] as const

export type StageState = 'todo' | 'done' | 'blocked' | 'locked'

/**
 * 阶段状态全部由真实 trace 推导，不靠界面自己记账。
 * "未执行"与"不通过"必须区分开——把前者显示成后者是危险的误导。
 */
export function stageStates(trace: Trace | null, procure: Procure | null): StageState[] {
  const s: StageState[] = ['done', 'done', 'done', 'locked', 'locked', 'locked', 'locked']
  if (!trace) return s

  const calcSteps = trace.steps.filter(
    x => x.kind !== 'check' && x.status !== 'not_applicable')
  const blockedCalc = calcSteps.some(x => x.status === 'data_missing' || x.status === 'needs_choice')
  s[3] = blockedCalc ? 'blocked' : 'done'

  const ranChecks = trace.checks
  const skippedChecks = trace.steps.some(x => x.kind === 'check' && x.status === 'skipped')
  if (ranChecks.length === 0) s[4] = 'locked'
  else if (ranChecks.some(c => !c.detail.passed)) s[4] = 'blocked'
  else s[4] = skippedChecks ? 'todo' : 'done'

  s[5] = trace.result.length > 0 ? 'done' : 'locked'
  s[6] = procure ? 'done' : 'locked'
  return s
}

const GLYPH: Record<StageState, string> = { done: '✓', blocked: '!', todo: '·', locked: '' }

export default function StepNav({ workflow, stage, onStage, states }: {
  workflow: Workflow | null
  stage: number
  onStage: (n: number) => void
  states: StageState[]
}) {
  return (
    <aside className="side card p-3 h-fit sticky top-[72px]">
      <div className="text-[11px] px-2 mb-2" style={{ color: 'var(--sub)' }}>
        {workflow ? `${workflow.name_zh} · 选型流程` : '选型流程'}
      </div>
      <div className="space-y-1">
        {STAGES.map(({ n, label }) => {
          const st = states[n]
          const locked = st === 'locked'
          const cls = ['step', stage === n ? 'on' : '', st === 'done' && stage !== n ? 'done' : '',
                       st === 'blocked' ? 'blocked' : ''].filter(Boolean).join(' ')
          return (
            <button key={n} className={cls} disabled={locked} onClick={() => onStage(n)}
                    title={locked ? '需要先完成前面的步骤' : undefined}>
              <span className="dot">{stage === n ? n : (GLYPH[st] || n)}</span>
              {n} {label}
            </button>
          )
        })}
      </div>

      {workflow && (
        <div className="mt-4 pt-3 border-t text-[11px] space-y-1"
             style={{ borderColor: 'var(--line)', color: 'var(--sub)' }}>
          <div>工作流：<span className="kbd">{workflow.material}.yaml</span></div>
          <div>信源：{workflow.standard || '—'}</div>
        </div>
      )}
    </aside>
  )
}

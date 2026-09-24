import type { Health } from '../types'

// guided 不进导航栏：它不是一个可以随时点进去的目的地，
// 而是首页认不出物料时才有的一条岔路（要带着 materialText）。
export type Page = 'home' | 'guided' | 'work' | 'knowledge' | 'settings'
export type Mode = 'online' | 'offline' | 'auto'

const NAV: { key: Page; label: string }[] = [
  { key: 'home', label: '项目中心' },
  { key: 'work', label: '选型工作台' },
  { key: 'knowledge', label: '知识库' },
  { key: 'settings', label: '设置' },
]

const MODES: { key: Mode; label: string; title: string }[] = [
  { key: 'auto', label: '自动', title: '有网且已绑定账号时启用 AI，否则自动降级到离线内核' },
  { key: 'online', label: '在线', title: '强制使用 AI 辅助（需已绑定 DeepSeek 账号）' },
  { key: 'offline', label: '离线', title: '只用本地确定性内核，不发起任何网络请求' },
]

/**
 * 顶栏状态徽章反映的是真实能力，不是装饰：
 * 未绑定账号 = AI 不可用 = 离线内核模式，此时计算/校核/出表/采购链接全部照常。
 */
function effectiveState(mode: Mode, health: Health | null) {
  if (!health) return { cls: 'b-warn', dot: '#fbbf24', text: '服务未连接' }
  if (mode === 'offline') return { cls: 'b-warn', dot: '#fbbf24', text: '离线（手动）' }
  if (!health.ai_bound) return { cls: 'b-warn', dot: '#fbbf24', text: '离线内核 · 未绑定账号' }
  if (!health.online) return { cls: 'b-warn', dot: '#fbbf24', text: '离线（网络不可达）' }
  return { cls: 'b-ok', dot: '#34d399', text: mode === 'auto' ? '自动 · 已连接' : '已连接' }
}

export default function Header({ page, onNavigate, mode, onMode, health }: {
  page: Page
  onNavigate: (p: Page) => void
  mode: Mode
  onMode: (m: Mode) => void
  health: Health | null
}) {
  const state = effectiveState(mode, health)

  return (
    <header className="sticky top-0 z-40 border-b"
            style={{ background: 'rgba(11,17,23,.92)', backdropFilter: 'blur(10px)', borderColor: 'var(--line)' }}>
      <div className="max-w-[1600px] mx-auto px-4 h-14 flex items-center gap-3">
        <button onClick={() => onNavigate('home')}
                className="flex items-center gap-2 font-semibold text-[15px]">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
          </svg>
          <span>机械选型引擎</span>
        </button>

        <div className="ml-2 md:ml-4 flex items-center gap-1 p-0.5 rounded-lg"
             style={{ background: 'var(--panel2)', border: '1px solid var(--line)' }}>
          {MODES.map(m => (
            <button key={m.key} onClick={() => onMode(m.key)} title={m.title}
                    className="px-3 py-1 rounded-md text-[12px] transition"
                    style={{
                      background: mode === m.key ? 'var(--brand)' : 'transparent',
                      color: mode === m.key ? '#fff' : 'var(--sub)',
                      fontWeight: mode === m.key ? 600 : 400,
                    }}>
              {m.label}
            </button>
          ))}
        </div>

        <span className={`badge ${state.cls} whitespace-nowrap`} title={
          health ? `引擎 v${health.engine_version} · ${health.workflows} 个可执行工作流` : undefined
        }>
          <span className="conf" style={{ background: state.dot }} />
          <span className="hidden sm:inline">{state.text}</span>
        </span>

        <div className="flex-1" />

        <nav className="hidden md:flex items-center gap-1 text-[13px]">
          {NAV.map(n => (
            <button key={n.key} onClick={() => onNavigate(n.key)} className="btn"
                    style={{
                      borderColor: page === n.key ? 'var(--brand)' : 'var(--line)',
                      color: page === n.key ? '#93c5fd' : 'var(--txt)',
                    }}>
              {n.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  )
}

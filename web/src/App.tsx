import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Header, { type Mode, type Page } from './components/Header'
import Guided from './pages/Guided'
import Home from './pages/Home'
import Knowledge from './pages/Knowledge'
import Settings from './pages/Settings'
import Workbench from './pages/Workbench'
import type { Health, Project } from './types'

const MODE_KEY = 'mds.mode'

function readMode(): Mode {
  try {
    const v = localStorage.getItem(MODE_KEY)
    if (v === 'online' || v === 'offline' || v === 'auto') return v
  } catch { /* 隐私模式下 localStorage 可能抛异常 */ }
  return 'auto'
}

export default function App() {
  const [page, setPage] = useState<Page>('home')
  const [mode, setMode] = useState<Mode>(readMode)
  const [health, setHealth] = useState<Health | null>(null)
  const [material, setMaterial] = useState<string | null>(null)
  const [project, setProject] = useState<Project | null>(null)

  const refresh = useCallback(() => {
    api.health(mode).then(setHealth).catch(() => setHealth(null))
  }, [mode])

  useEffect(refresh, [refresh])

  const changeMode = useCallback((m: Mode) => {
    setMode(m)
    try { localStorage.setItem(MODE_KEY, m) } catch { /* 忽略：模式只是本机偏好 */ }
  }, [])

  const [seed, setSeed] = useState<Record<string, unknown> | null>(null)
  // 引导式选型：认不出的物料走这条岔路。会话 id 非空时，工作台知道这份规格
  // **还没落盘**，要从会话里取规格、用会话的接口计算。
  const [guideText, setGuideText] = useState('')
  const [guidedSid, setGuidedSid] = useState<string | null>(null)

  const startMaterial = useCallback((id: string, values?: Record<string, unknown>) => {
    setMaterial(id); setProject(null); setSeed(values ?? null)
    setGuidedSid(null)
    setPage('work')
  }, [])

  const startGuided = useCallback((text: string, values?: Record<string, unknown>) => {
    setGuideText(text); setSeed(values ?? null); setGuidedSid(null); setPage('guided')
  }, [])

  const guidedReady = useCallback((sid: string, id: string) => {
    setMaterial(id); setProject(null); setGuidedSid(sid); setPage('work')
  }, [])

  const openProject = useCallback(async (p: Project) => {
    const full = await api.project(p.id).catch(() => p)
    setMaterial(full.material); setProject(full); setSeed(null)
    setGuidedSid(null)
    setPage('work')
  }, [])

  return (
    <div className="min-h-screen">
      <Header page={page} onNavigate={setPage} mode={mode} onMode={changeMode} health={health} />
      <main className="max-w-[1600px] mx-auto px-4 py-5">
        {page === 'home' && (
          <Home onStart={startMaterial} onGuide={startGuided} onOpen={openProject}
                aiBound={!!health?.ai_bound} />
        )}
        {page === 'guided' && (
          <Guided key={guideText} materialText={guideText}
                  onReady={guidedReady} onCancel={() => setPage('home')} />
        )}
        {page === 'work' && (
          material
            ? <Workbench key={`${material}:${project?.id ?? guidedSid ?? 'new'}`}
                         material={material} initialProject={project} health={health}
                         seed={seed} mode={mode} guidedSid={guidedSid}
                         onGoSettings={() => setPage('settings')}
                         onGoKnowledge={() => setPage('knowledge')} />
            : <div className="card p-8 text-center text-[13px]" style={{ color: 'var(--sub)' }}>
                请先从项目中心选择一个物料。
                <div className="mt-3">
                  <button className="btn btn-p" onClick={() => setPage('home')}>去项目中心 →</button>
                </div>
              </div>
        )}
        {page === 'knowledge' && <Knowledge />}
        {page === 'settings' && <Settings health={health} onChanged={refresh} />}
      </main>
    </div>
  )
}

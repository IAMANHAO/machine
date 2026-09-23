import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Header, { type Mode, type Page } from './components/Header'
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

  const startMaterial = useCallback((id: string, values?: Record<string, unknown>) => {
    setMaterial(id); setProject(null); setSeed(values ?? null); setPage('work')
  }, [])

  const openProject = useCallback(async (p: Project) => {
    const full = await api.project(p.id).catch(() => p)
    setMaterial(full.material); setProject(full); setSeed(null); setPage('work')
  }, [])

  return (
    <div className="min-h-screen">
      <Header page={page} onNavigate={setPage} mode={mode} onMode={changeMode} health={health} />
      <main className="max-w-[1600px] mx-auto px-4 py-5">
        {page === 'home' && (
          <Home onStart={startMaterial} onOpen={openProject}
                aiBound={!!health?.ai_bound} />
        )}
        {page === 'work' && (
          material
            ? <Workbench key={`${material}:${project?.id ?? 'new'}`}
                         material={material} initialProject={project} health={health}
                         seed={seed} mode={mode}
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

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { Library } from 'lucide-react'
import { api } from './api'
import { useRoute } from './lib/hooks'
import { LoadError, Spinner, ToastHost } from './components/ui'
import { LibraryPage } from './pages/LibraryPage'
import { SongPage } from './pages/SongPage'
import { DEFAULT_SETTINGS, type Meta, type Settings } from '../shared/types'

interface AppState {
  meta: Meta
  settings: Settings
  saveSettings: (patch: Partial<Settings>) => Promise<void>
}

const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const state = useContext(AppContext)
  if (!state) throw new Error('useApp must be used inside the app')
  return state
}

function NavLink({ href, active, icon, children }: { href: string; active: boolean; icon: ReactNode; children: ReactNode }) {
  return (
    <a href={href} className={`nav-link${active ? ' is-active' : ''}`} aria-current={active ? 'page' : undefined}>
      {icon}
      <span>{children}</span>
    </a>
  )
}

export function App() {
  const route = useRoute()
  const [meta, setMeta] = useState<Meta | null>(null)
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS)
  const [error, setError] = useState<string | null>(null)

  const boot = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api.meta(), api.settings()])
      setMeta(m)
      setSettings(s)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void boot()
  }, [boot])

  // A button clicked with the mouse should not keep the focus, or Space would press it again
  // instead of playing and pausing the song. Buttons reached with the keyboard are left alone.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.detail === 0 || !(e.target instanceof Element)) return
      const control = e.target.closest('button, summary')
      if (control instanceof HTMLElement && document.activeElement === control) control.blur()
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [])

  const saveSettings = useCallback(async (patch: Partial<Settings>) => {
    setSettings((s) => ({ ...s, ...patch }))
    setSettings(await api.saveSettings(patch))
  }, [])

  const page = route[0] ?? 'library'

  let content: ReactNode
  if (error) content = <LoadError error={error} onRetry={boot} />
  else if (!meta) content = <Spinner />
  else if (page === 'song' && route[1]) content = <SongPage key={route[1]} songId={route[1]} />
  else content = <LibraryPage />

  return (
    <ToastHost>
      <div className="app">
        <header className="topbar">
          <a href="#/" className="brand">
            <span className="brand-mark" lang="ar" aria-hidden="true">
              غ
            </span>
            <span className="brand-name">Song Lab</span>
            <span className="brand-sub">Sudanese Arabic</span>
          </a>
          <nav className="nav" aria-label="Main">
            <NavLink href="#/" active={page === 'library' || page === 'song'} icon={<Library size={16} />}>
              Library
            </NavLink>
          </nav>
        </header>
        <main className="main">
          {meta && !error ? (
            <AppContext.Provider value={{ meta, settings, saveSettings }}>{content}</AppContext.Provider>
          ) : (
            content
          )}
        </main>
      </div>
    </ToastHost>
  )
}

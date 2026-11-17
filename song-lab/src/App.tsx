import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { GraduationCap, Layers, Library } from 'lucide-react'
import { api } from './api'
import { useRoute } from './lib/hooks'
import { LoadError, Spinner, ToastHost } from './components/ui'
import { LibraryPage } from './pages/LibraryPage'
import { SongPage } from './pages/SongPage'
import { ReviewPage } from './pages/ReviewPage'
import { DeckPage } from './pages/DeckPage'
import { TutorPage } from './pages/TutorPage'
import { DEFAULT_SETTINGS, type Meta, type Settings } from '../shared/types'

interface AppState {
  meta: Meta
  settings: Settings
  saveSettings: (patch: Partial<Settings>) => Promise<void>
  /** Cards waiting in today's review, for the badge in the navigation. */
  dueCount: number
  refreshCounts: () => void
}

const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const state = useContext(AppContext)
  if (!state) throw new Error('useApp must be used inside the app')
  return state
}

function NavLink({
  href,
  active,
  icon,
  badge,
  children,
}: {
  href: string
  active: boolean
  icon: ReactNode
  badge?: number
  children: ReactNode
}) {
  return (
    <a href={href} className={`nav-link${active ? ' is-active' : ''}`} aria-current={active ? 'page' : undefined}>
      {icon}
      <span>{children}</span>
      {badge ? <span className="nav-badge">{badge}</span> : null}
    </a>
  )
}

export function App() {
  const route = useRoute()
  const [meta, setMeta] = useState<Meta | null>(null)
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS)
  const [error, setError] = useState<string | null>(null)
  const [dueCount, setDueCount] = useState(0)

  const refreshCounts = useCallback(() => {
    api
      .queue()
      .then((queue) => setDueCount(queue.cards.length))
      .catch(() => {})
  }, [])

  const boot = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api.meta(), api.settings()])
      setMeta(m)
      setSettings(s)
      setError(null)
      refreshCounts()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [refreshCounts])

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
  else if (page === 'review') content = <ReviewPage />
  else if (page === 'deck') content = <DeckPage />
  else if (page === 'tutor') content = <TutorPage key={route[1] ?? 'queue'} print={route[1] === 'print'} />
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
            <NavLink href="#/review" active={page === 'review' || page === 'deck'} icon={<Layers size={16} />} badge={dueCount}>
              Review
            </NavLink>
            <NavLink href="#/tutor" active={page === 'tutor'} icon={<GraduationCap size={16} />}>
              Tutor
            </NavLink>
          </nav>
        </header>
        <main className="main">
          {meta && !error ? (
            <AppContext.Provider value={{ meta, settings, saveSettings, dueCount, refreshCounts }}>{content}</AppContext.Provider>
          ) : (
            content
          )}
        </main>
      </div>
    </ToastHost>
  )
}

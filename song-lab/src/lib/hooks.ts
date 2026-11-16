import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { SongPlayer, type PlayerSnapshot } from './player'

// ---- hash routing ----

function readHash(): string[] {
  return window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
}

/** The current route as path segments: "#/song/abc" gives ["song", "abc"]. */
export function useRoute(): string[] {
  const [route, setRoute] = useState(readHash)
  useEffect(() => {
    const onChange = () => setRoute(readHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function go(path: string): void {
  window.location.hash = path
}

// ---- data loading ----

export interface Loaded<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => Promise<void>
  setData: (next: T | ((prev: T) => T)) => void
}

/** Load something from the server and keep it in state. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [data, setDataState] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  loadRef.current = load
  const generation = useRef(0)

  const reload = useCallback(async () => {
    const mine = ++generation.current
    try {
      const result = await loadRef.current()
      if (mine !== generation.current) return
      setDataState(result)
      setError(null)
    } catch (err) {
      if (mine !== generation.current) return
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    setDataState(null)
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const setData = useCallback((next: T | ((prev: T) => T)) => {
    setDataState((prev) => (typeof next === 'function' ? (prev === null ? prev : (next as (p: T) => T)(prev)) : next))
  }, [])

  return { data, error, loading, reload, setData }
}

// ---- YouTube player ----

const IDLE: PlayerSnapshot = {
  status: 'loading',
  error: null,
  playing: false,
  buffering: false,
  rate: 1,
  rates: [],
  duration: 0,
  loop: null,
}
const noSubscribe = () => () => {}
const idleSnapshot = () => IDLE

/** Create a player for a video inside the element the returned ref is attached to. */
export function usePlayer(videoId: string | null, initialRate: number) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [player, setPlayer] = useState<SongPlayer | null>(null)
  const [attempt, setAttempt] = useState(0)
  const rateRef = useRef(initialRate)
  rateRef.current = initialRate

  useEffect(() => {
    if (!videoId || !hostRef.current) return
    const instance = new SongPlayer(hostRef.current, videoId, rateRef.current)
    setPlayer(instance)
    return () => {
      instance.destroy()
      setPlayer(null)
    }
  }, [videoId, attempt])

  const snap = useSyncExternalStore(player?.subscribe ?? noSubscribe, player?.getSnapshot ?? idleSnapshot)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  return { hostRef, player, snap, retry }
}

// ---- keyboard ----

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/**
 * Page-wide shortcuts. Keys are KeyboardEvent.key values ("ArrowUp", " ", "l").
 * Shortcuts stay quiet while the user is typing in a field or a dialog is open.
 */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>, enabled = true): void {
  const mapRef = useRef(map)
  mapRef.current = map
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (isTyping(e.target) || document.querySelector('dialog[open]')) return
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key
      const handler = mapRef.current[key]
      if (!handler) return
      e.preventDefault()
      handler(e)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}

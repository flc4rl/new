import { useCallback, useEffect, useState } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'songlab.theme'
const query = () => window.matchMedia('(prefers-color-scheme: dark)')

function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

function apply(choice: ThemeChoice): void {
  const theme = choice === 'system' ? (query().matches ? 'dark' : 'light') : choice
  document.documentElement.dataset.theme = theme
}

/** The chosen theme, applied to the page and remembered in this browser. */
export function useTheme() {
  const [choice, setChoiceState] = useState<ThemeChoice>(readChoice)

  useEffect(() => {
    apply(choice)
    if (choice !== 'system') return
    // Follow the computer when it switches between light and dark.
    const media = query()
    const onChange = () => apply('system')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [choice])

  // Keep every user of the hook in step.
  useEffect(() => {
    const onSync = () => setChoiceState(readChoice())
    window.addEventListener('songlab-theme', onSync)
    return () => window.removeEventListener('songlab-theme', onSync)
  }, [])

  const setChoice = useCallback((next: ThemeChoice) => {
    try {
      if (next === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, next)
    } catch {
      // Without storage the choice lasts until the page is reloaded.
    }
    setChoiceState(next)
    window.dispatchEvent(new Event('songlab-theme'))
  }, [])

  return { choice, setChoice }
}

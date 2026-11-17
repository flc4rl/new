import { useCallback, useEffect, useState } from 'react'

/** Sizes for Arabic text, as a multiple of the original size. */
export const AR_SCALES = [1, 1.25, 1.5, 1.8, 2.2, 2.7]
export const DEFAULT_AR_SCALE = 1.5
const KEY = 'songlab.arabicScale'

function read(): number {
  try {
    const n = Number(localStorage.getItem(KEY))
    return AR_SCALES.includes(n) ? n : DEFAULT_AR_SCALE
  } catch {
    return DEFAULT_AR_SCALE
  }
}

/** The Arabic size, applied to the whole app and remembered in this browser. */
export function useArabicSize() {
  const [scale, setScaleState] = useState(read)

  useEffect(() => {
    document.documentElement.style.setProperty('--ar-scale', String(scale))
  }, [scale])

  // Keep every control in step.
  useEffect(() => {
    const sync = () => setScaleState(read())
    window.addEventListener('songlab-arabic-size', sync)
    return () => window.removeEventListener('songlab-arabic-size', sync)
  }, [])

  const setScale = useCallback((next: number) => {
    try {
      localStorage.setItem(KEY, String(next))
    } catch {
      // Without storage the size lasts until the page is reloaded.
    }
    setScaleState(next)
    window.dispatchEvent(new Event('songlab-arabic-size'))
  }, [])

  const index = AR_SCALES.indexOf(scale)
  return {
    scale,
    setScale,
    smaller: index > 0 ? () => setScale(AR_SCALES[index - 1]) : null,
    bigger: index < AR_SCALES.length - 1 ? () => setScale(AR_SCALES[index + 1]) : null,
  }
}

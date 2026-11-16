import { useEffect, useLayoutEffect, useRef, useState } from 'react'

const AUTO_SIZES = typeof CSS !== 'undefined' && CSS.supports('field-sizing', 'content')
const SAVE_DELAY_MS = 900

/**
 * A line of text that reads like plain text and edits in place.
 * It saves when the field loses focus, on Enter, and shortly after typing stops.
 */
export function EditableText({
  value,
  onSave,
  label,
  placeholder,
  className,
  rtl,
}: {
  value: string
  onSave: (next: string) => void
  label: string
  placeholder?: string
  className?: string
  /** Arabic script: right to left, in the Arabic typeface. */
  rtl?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const ref = useRef<HTMLTextAreaElement>(null)
  const focused = useRef(false)
  const timer = useRef<number | undefined>(undefined)
  const latest = useRef({ draft, value, onSave })
  latest.current = { draft, value, onSave }

  // Follow outside changes (a Claude draft, a tutor correction) unless the user is typing here.
  useEffect(() => {
    if (!focused.current) setDraft(value)
  }, [value])

  useLayoutEffect(() => {
    const el = ref.current
    if (!el || AUTO_SIZES) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  function commit() {
    window.clearTimeout(timer.current)
    const next = latest.current.draft.replace(/\s+/g, ' ').trim()
    if (next !== latest.current.value) latest.current.onSave(next)
  }

  // Save a pending edit if the row goes away while the field still has focus.
  useEffect(() => () => {
    if (focused.current) commit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <textarea
      ref={ref}
      className={`editable${rtl ? ' ar' : ' ltr'}${className ? ` ${className}` : ''}`}
      rows={1}
      value={draft}
      aria-label={label}
      placeholder={placeholder}
      dir={rtl ? 'rtl' : 'ltr'}
      lang={rtl ? 'ar' : undefined}
      // Capitals carry meaning in the transliteration, so the browser must not add any.
      autoCapitalize="off"
      autoCorrect="off"
      autoComplete="off"
      spellCheck={false}
      onFocus={() => {
        focused.current = true
      }}
      onChange={(e) => {
        setDraft(e.target.value.replace(/\n/g, ' '))
        window.clearTimeout(timer.current)
        timer.current = window.setTimeout(commit, SAVE_DELAY_MS)
      }}
      onBlur={() => {
        focused.current = false
        commit()
        setDraft((d) => d.replace(/\s+/g, ' ').trim())
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          e.currentTarget.blur()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          window.clearTimeout(timer.current)
          setDraft(value)
          latest.current.draft = value
          e.currentTarget.blur()
        }
      }}
    />
  )
}

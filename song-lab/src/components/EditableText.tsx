import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

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
  view,
}: {
  value: string
  onSave: (next: string) => void
  label: string
  placeholder?: string
  className?: string
  /** Arabic script: right to left, in the Arabic typeface. */
  rtl?: boolean
  /**
   * What to show while the text is not being edited, such as words that light up on hover.
   * A click or the Tab key switches to editing.
   */
  view?: ReactNode
}) {
  const [draft, setDraft] = useState(value)
  const [editing, setEditing] = useState(false)
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

  const classes = `editable${rtl ? ' ar' : ' ltr'}${className ? ` ${className}` : ''}`

  if (view && value && !editing) {
    return (
      <div
        className={`${classes} editable-view`}
        dir={rtl ? 'rtl' : 'ltr'}
        lang={rtl ? 'ar' : undefined}
        tabIndex={0}
        aria-label={`${label}: ${value}. Click to edit.`}
        onFocus={() => setEditing(true)}
      >
        {view}
      </div>
    )
  }

  return (
    <textarea
      ref={ref}
      autoFocus={!!view && editing}
      className={classes}
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
      onFocus={(e) => {
        focused.current = true
        // Coming from the word view, put the cursor at the end.
        if (view) e.currentTarget.setSelectionRange(e.currentTarget.value.length, e.currentTarget.value.length)
      }}
      onChange={(e) => {
        setDraft(e.target.value.replace(/\n/g, ' '))
        window.clearTimeout(timer.current)
        timer.current = window.setTimeout(commit, SAVE_DELAY_MS)
      }}
      onBlur={() => {
        focused.current = false
        setEditing(false)
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

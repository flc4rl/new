import { useEffect, useRef, useState } from 'react'

/** A one-line input that saves when it loses focus or Enter is pressed. Escape puts the old value back. */
export function CommitInput({
  value,
  onCommit,
  label,
  placeholder,
  rtl,
  className,
  autoFocus,
}: {
  value: string
  onCommit: (next: string) => void
  label: string
  placeholder?: string
  rtl?: boolean
  className?: string
  autoFocus?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const focused = useRef(false)
  const cancelled = useRef(false)

  useEffect(() => {
    if (!focused.current) setDraft(value)
  }, [value])

  return (
    <input
      className={`input${rtl ? ' ar' : ' ltr'}${className ? ` ${className}` : ''}`}
      value={draft}
      aria-label={label}
      placeholder={placeholder}
      dir={rtl ? 'rtl' : 'ltr'}
      lang={rtl ? 'ar' : undefined}
      autoFocus={autoFocus}
      // Capitals carry meaning in the transliteration, so the browser must not add any.
      autoCapitalize="off"
      autoCorrect="off"
      autoComplete="off"
      spellCheck={false}
      onFocus={() => {
        focused.current = true
        cancelled.current = false
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        focused.current = false
        if (cancelled.current) {
          setDraft(value)
          return
        }
        const next = draft.replace(/\s+/g, ' ').trim()
        setDraft(next)
        if (next !== value) onCommit(next)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        else if (e.key === 'Escape') {
          cancelled.current = true
          e.currentTarget.blur()
        }
      }}
    />
  )
}

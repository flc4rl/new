import { useState } from 'react'
import { Button, Modal } from './ui'
import { splitLines } from '../../shared/text'

export function PasteLyricsDialog({
  hasLines,
  autoTranslate,
  onAdd,
  onClose,
}: {
  hasLines: boolean
  autoTranslate: boolean
  onAdd: (text: string) => Promise<void>
  onClose: () => void
}) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const count = splitLines(text).length

  async function submit() {
    setSaving(true)
    try {
      await onAdd(text)
      onClose()
    } catch {
      // The page has already shown what went wrong; keep the text so nothing is lost.
      setSaving(false)
    }
  }

  return (
    <Modal title={hasLines ? 'Add more lyrics' : 'Paste the lyrics'} onClose={onClose} wide>
      <p className="muted">
        Type or paste the Arabic, one line of the song per row. {hasLines ? 'New lines are added at the end. ' : ''}
        Lyrics stay on this computer, in the app’s data folder.
        {autoTranslate &&
          ' Claude then translates them for you: transliteration, English and words, saved as drafts for you to check.'}
      </p>
      <textarea
        className="input ar paste-area"
        dir="rtl"
        lang="ar"
        rows={12}
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoFocus
        spellCheck={false}
        aria-label="Lyrics, one line per row"
      />
      <footer className="modal-foot">
        <span className="muted push">{count === 0 ? 'No lines yet' : `${count} ${count === 1 ? 'line' : 'lines'}`}</span>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={count === 0 || saving} onClick={submit}>
          {saving ? 'Adding…' : count > 0 ? `Add ${count} ${count === 1 ? 'line' : 'lines'}` : 'Add lines'}
        </Button>
      </footer>
    </Modal>
  )
}

import { useState, type FormEvent } from 'react'
import { api } from '../api'
import { useApp } from '../App'
import { Button, Modal, useToast } from './ui'
import type { Direction } from '../../shared/types'

/** A phrase from a lesson or the coursebook, typed in by hand. It has no audio. */
export function AddPhraseDialog({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const toast = useToast()
  const { settings, refreshCounts } = useApp()
  const [arabic, setArabic] = useState('')
  const [translit, setTranslit] = useState('')
  const [gloss, setGloss] = useState('')
  const [directions, setDirections] = useState<'both' | Direction>(settings.cardDirections)
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const created = await api.addCards(
        { kind: 'phrase', arabic, translit, gloss },
        directions === 'both' ? ['ar-en', 'en-ar'] : [directions],
      )
      toast.info(created.length ? 'Phrase added to the deck.' : 'That phrase is already in the deck.')
      refreshCounts()
      onAdded()
    } catch (err) {
      toast.error(err)
      setSaving(false)
    }
  }

  return (
    <Modal title="Add a phrase" onClose={onClose}>
      <form className="form-grid" onSubmit={submit}>
        <p className="muted small">For phrases from a lesson or the coursebook. They have no audio.</p>
        <label className="field">
          <span>Arabic</span>
          <input className="ar phrase-ar" dir="rtl" lang="ar" value={arabic} onChange={(e) => setArabic(e.target.value)} required autoFocus />
        </label>
        <label className="field">
          <span>Transliteration</span>
          <input
            className="ltr"
            value={translit}
            onChange={(e) => setTranslit(e.target.value)}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
        </label>
        <label className="field">
          <span>English</span>
          <input className="ltr" value={gloss} onChange={(e) => setGloss(e.target.value)} required />
        </label>
        <label className="field">
          <span>Review it</span>
          <select value={directions} onChange={(e) => setDirections(e.target.value as 'both' | Direction)}>
            <option value="both">both ways</option>
            <option value="ar-en">Arabic to English only</option>
            <option value="en-ar">English to Arabic only</option>
          </select>
        </label>
        <footer className="modal-foot">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving || !arabic.trim() || !gloss.trim()}>
            Add phrase
          </Button>
        </footer>
      </form>
    </Modal>
  )
}

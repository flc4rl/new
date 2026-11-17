import { useMemo, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api } from '../api'
import { Button, Modal, Spinner, useToast } from './ui'
import type { DraftResult, Line, Word } from '../../shared/types'

/** A line is worth drafting while it is not verified and something is still missing. */
function needsDraft(line: Line, words: Word[]): boolean {
  if (!line.arabic.trim()) return false
  const lineGaps = line.status === 'draft' && (!line.translit || !line.gloss)
  const own = words.filter((w) => w.lineId === line.id)
  const wordGaps = own.length === 0 || own.some((w) => w.status === 'draft' && (!w.translit || !w.gloss))
  return lineGaps || wordGaps
}

export function DraftDialog({
  songId,
  lines,
  words,
  model,
  onDone,
  onClose,
}: {
  songId: string
  lines: Line[]
  words: Word[]
  model: string
  onDone: () => void
  onClose: () => void
}) {
  const toast = useToast()
  const suggested = useMemo(() => new Set(lines.filter((l) => needsDraft(l, words)).map((l) => l.id)), [lines, words])
  const [chosen, setChosen] = useState<Set<string>>(suggested)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<DraftResult | null>(null)

  const toggle = (id: string) =>
    setChosen((set) => {
      const next = new Set(set)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  async function run() {
    setRunning(true)
    try {
      const ids = lines.filter((l) => chosen.has(l.id)).map((l) => l.id)
      setResult(await api.draft(songId, ids))
      onDone()
    } catch (err) {
      toast.error(err)
    } finally {
      setRunning(false)
    }
  }

  if (result) {
    return (
      <Modal title="Drafts saved" onClose={onClose}>
        <div className="modal-text">
          <p>
            Claude drafted {result.updatedLines} {result.updatedLines === 1 ? 'line' : 'lines'} and {result.updatedWords}{' '}
            {result.updatedWords === 1 ? 'word' : 'words'}. Everything is marked as a draft, and nothing you had typed
            or verified was changed.
          </p>
          <p>Lines and words Claude was not sure about are tagged “Claude unsure”. The tutor queue lists them all.</p>
          {result.skipped.length > 0 && (
            <ul className="draft-skipped">
              {result.skipped.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </div>
        <footer className="modal-foot">
          <a className="btn btn-default btn-md push" href="#/tutor">
            <span>Open the tutor queue</span>
          </a>
          <Button variant="primary" onClick={onClose} autoFocus>
            Done
          </Button>
        </footer>
      </Modal>
    )
  }

  return (
    <Modal title="Draft with Claude" onClose={running ? () => {} : onClose} wide>
      <div className="modal-text">
        <p>
          Claude fills in the transliteration, the English gloss and the words of the lines you tick, in Khartoum
          pronunciation and the app’s transliteration scheme. Only empty fields are filled; everything is saved as a
          draft for you or your tutor to check. Claude never adds or completes lyrics.
        </p>
      </div>
      <div className="draft-pick">
        <span className="muted small">
          {chosen.size} of {lines.length} lines
        </span>
        <button type="button" className="chip-btn" onClick={() => setChosen(new Set(suggested))} disabled={running}>
          Lines with gaps ({suggested.size})
        </button>
        <button type="button" className="chip-btn" onClick={() => setChosen(new Set(lines.map((l) => l.id)))} disabled={running}>
          All
        </button>
        <button type="button" className="chip-btn" onClick={() => setChosen(new Set())} disabled={running}>
          None
        </button>
      </div>
      <ol className="draft-lines">
        {lines.map((line, i) => (
          <li key={line.id}>
            <label className={line.status === 'verified' ? 'is-verified' : ''}>
              <input type="checkbox" checked={chosen.has(line.id)} onChange={() => toggle(line.id)} disabled={running || !line.arabic} />
              <span className="draft-num">{i + 1}</span>
              <span className="ar draft-ar" lang="ar">
                {line.arabic || '…'}
              </span>
              <span className="draft-state">
                {line.status === 'verified' ? 'verified' : line.translit && line.gloss ? 'has text' : 'gaps'}
              </span>
            </label>
          </li>
        ))}
      </ol>
      <footer className="modal-foot">
        {running ? (
          <span className="push">
            <Spinner label="Claude is working. This can take a minute or two." />
          </span>
        ) : (
          <span className="muted small push">Model: {model}</span>
        )}
        <Button onClick={onClose} disabled={running}>
          Cancel
        </Button>
        <Button variant="primary" icon={<Sparkles size={15} />} onClick={run} disabled={running || chosen.size === 0}>
          Draft {chosen.size} {chosen.size === 1 ? 'line' : 'lines'}
        </Button>
      </footer>
    </Modal>
  )
}

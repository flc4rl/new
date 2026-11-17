import { useRef, useState } from 'react'
import { Download, Upload } from 'lucide-react'
import { api } from '../api'
import { useApp } from '../App'
import { useTheme, type ThemeChoice } from '../lib/theme'
import { Button, Confirm, useToast } from '../components/ui'
import type { Settings } from '../../shared/types'

interface PendingImport {
  name: string
  data: unknown
  counts: { songs: number; lines: number; cards: number; reviews: number; recordings: number; withAudio: number }
}

function countOf(data: unknown, key: string): number {
  const value = (data as Record<string, unknown>)?.[key]
  return Array.isArray(value) ? value.length : 0
}

function BackupSection() {
  const toast = useToast()
  const [withAudio, setWithAudio] = useState(true)
  const [pending, setPending] = useState<PendingImport | null>(null)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function pick(file: File) {
    try {
      const data = JSON.parse(await file.text()) as Record<string, unknown>
      if (data?.app !== 'sudanese-arabic-song-lab') throw new Error('That file is not a Song Lab backup.')
      const recordings = Array.isArray(data.recordings) ? (data.recordings as { dataBase64?: string }[]) : []
      setPending({
        name: file.name,
        data,
        counts: {
          songs: countOf(data, 'songs'),
          lines: countOf(data, 'lines'),
          cards: countOf(data, 'cards'),
          reviews: countOf(data, 'reviews'),
          recordings: recordings.length,
          withAudio: recordings.filter((r) => r.dataBase64).length,
        },
      })
    } catch (err) {
      toast.error(err instanceof SyntaxError ? new Error('That file is not valid JSON.') : err)
    } finally {
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function restore() {
    if (!pending) return
    setImporting(true)
    try {
      await api.importBackup(pending.data)
      // Start the app afresh on the restored data.
      window.location.hash = '#/'
      window.location.reload()
    } catch (err) {
      toast.error(err)
      setImporting(false)
      setPending(null)
    }
  }

  return (
    <section className="panel settings-section">
      <h2>Backup</h2>
      <p className="muted">
        One JSON file with every song, line, word, card, review, question and setting. Keep it somewhere safe; it holds
        your lyrics, so do not commit it to a repository.
      </p>
      <div className="stage-row">
        <a className="btn btn-primary btn-md" href={`/api/backup/export${withAudio ? '' : '?audio=0'}`} download>
          <Download size={16} />
          <span>Download backup</span>
        </a>
        <label className="check">
          <input type="checkbox" checked={withAudio} onChange={(e) => setWithAudio(e.target.checked)} />
          include your recordings
        </label>
      </div>
      <div className="stage-row">
        <Button icon={<Upload size={16} />} onClick={() => fileRef.current?.click()}>
          Restore from a backup…
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void pick(file)
          }}
        />
      </div>
      {pending && (
        <Confirm
          title="Replace everything with this backup?"
          confirmLabel={importing ? 'Restoring…' : 'Replace and restore'}
          danger
          onClose={() => !importing && setPending(null)}
          onConfirm={() => void restore()}
        >
          <p>
            <b>{pending.name}</b> holds {pending.counts.songs} songs, {pending.counts.lines} lines, {pending.counts.cards}{' '}
            cards with {pending.counts.reviews} reviews, and {pending.counts.recordings} recordings
            {pending.counts.recordings > pending.counts.withAudio ? ` (${pending.counts.withAudio} with audio)` : ''}.
          </p>
          <p>Everything now in the app is replaced by it. Download a backup first if you may want the current state back.</p>
        </Confirm>
      )}
    </section>
  )
}

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: 'system', label: 'Same as the computer' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

function NumberSetting({
  label,
  hint,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  hint: string
  value: number
  min: number
  max: number
  step: number
  onChange: (n: number) => void
}) {
  return (
    <label className="setting-row">
      <span>
        <b>{label}</b>
        <small className="muted">{hint}</small>
      </span>
      <input
        type="number"
        className="input"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const n = Number(e.target.value)
          if (Number.isFinite(n) && n >= min && n <= max) onChange(n)
        }}
      />
    </label>
  )
}

export function SettingsPage() {
  const toast = useToast()
  const { meta, settings, saveSettings } = useApp()
  const { choice, setChoice } = useTheme()
  const save = (patch: Partial<Settings>) => saveSettings(patch).catch(toast.error)

  return (
    <div className="page page-narrow settings">
      <h1>Settings</h1>

      <BackupSection />

      <section className="panel settings-section">
        <h2>Appearance</h2>
        <div className="segmented" role="group" aria-label="Theme">
          {THEMES.map((t) => (
            <button key={t.value} type="button" aria-pressed={choice === t.value} onClick={() => setChoice(t.value)}>
              {t.label}
            </button>
          ))}
        </div>
      </section>

      <section className="panel settings-section">
        <h2>Timing</h2>
        <NumberSetting
          label="Tap delay correction (seconds)"
          hint="Taken off every sync tap, because a tap lands a little after the sound. Raise it if lines start late."
          value={settings.tapOffsetSec}
          min={0}
          max={1}
          step={0.05}
          onChange={(tapOffsetSec) => void save({ tapOffsetSec })}
        />
        <NumberSetting
          label="Loop lead-in (seconds)"
          hint="Loops and clips start this much before the line, so the first syllable is not clipped."
          value={settings.loopLeadInSec}
          min={0}
          max={2}
          step={0.05}
          onChange={(loopLeadInSec) => void save({ loopLeadInSec })}
        />
      </section>

      <section className="panel settings-section">
        <h2>Claude drafts</h2>
        {meta.claude ? (
          <p>
            On. Drafts use <code>{meta.model}</code>. The API key stays on this computer’s server and never reaches the
            browser.
          </p>
        ) : (
          <p>
            Off. To turn them on, set the <code>ANTHROPIC_API_KEY</code> environment variable (or put it in a{' '}
            <code>.env</code> file in the app folder) and restart the app. The README explains how. Everything else works
            without it.
          </p>
        )}
      </section>

      <section className="panel settings-section">
        <h2>Transliteration scheme</h2>
        <p className="muted">
          To match your tutor’s or the coursebook’s convention, edit <code>translit.config.json</code> in the app
          folder. Claude drafts follow it.
        </p>
        <ul className="scheme-rules">
          {meta.translit.rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
        <table className="scheme">
          <thead>
            <tr>
              <th>Arabic</th>
              <th>Write</th>
              <th>Sound in Khartoum speech</th>
            </tr>
          </thead>
          <tbody>
            {meta.translit.letters.map((l) => (
              <tr key={l.arabic}>
                <td className="ar scheme-ar" lang="ar">
                  {l.arabic}
                </td>
                <td>
                  <code>{l.write}</code>
                </td>
                <td>{l.sound}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted">{meta.translit.otherConsonants}</p>
      </section>
    </div>
  )
}

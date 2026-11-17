import { CornerDownLeft, Delete, Repeat, Timer } from 'lucide-react'
import type { PlayerSnapshot } from '../lib/player'
import { Button, Kbd } from './ui'
import type { Line, Settings } from '../../shared/types'

const REPEAT_CHOICES = [1, 2, 3, 5, 10, 20, 0]
const PAUSE_CHOICES = [0, 0.5, 1, 2, 3, 5, 8]

export function LoopPanel({
  snap,
  settings,
  looping,
  canLoop,
  onToggle,
  onSettings,
}: {
  snap: PlayerSnapshot
  settings: Settings
  looping: boolean
  canLoop: boolean
  onToggle: () => void
  onSettings: (patch: Partial<Settings>) => void
}) {
  const loop = snap.loop
  return (
    <section className="stage-section">
      <h2>Loop</h2>
      <div className="stage-row">
        <Button
          icon={<Repeat size={15} />}
          pressed={looping}
          disabled={!canLoop && !looping}
          onClick={onToggle}
          title={canLoop ? 'Loop the highlighted line (L)' : 'Time the line first, with tap-to-sync'}
        >
          {looping ? 'Stop loop' : 'Loop line'}
        </Button>
        <label className="inline-field">
          <span>Repeats</span>
          <select
            value={settings.loopRepeats}
            onChange={(e) => {
              onSettings({ loopRepeats: Number(e.target.value) })
              e.currentTarget.blur()
            }}
            aria-label="Number of repeats"
          >
            {REPEAT_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? 'until stopped' : `${n}×`}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-field">
          <span>Pause</span>
          <select
            value={settings.loopPauseSec}
            onChange={(e) => {
              onSettings({ loopPauseSec: Number(e.target.value) })
              e.currentTarget.blur()
            }}
            aria-label="Pause between repeats"
          >
            {PAUSE_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? 'none' : `${n} s`}
              </option>
            ))}
          </select>
        </label>
        {looping && loop && (
          <span className="loop-count" aria-live="polite">
            {loop.waiting ? 'pause · ' : ''}
            {Math.min(loop.done + (loop.waiting ? 0 : 1), Number.isFinite(loop.repeats) ? loop.repeats : loop.done + 1)}
            {Number.isFinite(loop.repeats) ? ` of ${loop.repeats}` : ''}
          </span>
        )}
      </div>
    </section>
  )
}

export function SyncPanel({
  lines,
  syncing,
  syncIndex,
  playing,
  onStart,
  onTap,
  onUndo,
  onDone,
}: {
  lines: Line[]
  syncing: boolean
  syncIndex: number
  playing: boolean
  onStart: () => void
  onTap: () => void
  onUndo: () => void
  onDone: () => void
}) {
  const timed = lines.filter((l) => l.startSec != null).length
  const next = lines[syncIndex]
  if (!syncing) {
    return (
      <section className="stage-section">
        <h2>Sync</h2>
        <div className="stage-row">
          <Button icon={<Timer size={15} />} disabled={lines.length === 0} onClick={onStart} title="Tap-to-sync (S)">
            {timed === 0 ? 'Tap to sync' : timed < lines.length ? 'Continue syncing' : 'Sync again'}
          </Button>
          <span className="muted small">
            {lines.length === 0 ? 'Add lyrics first.' : `${timed} of ${lines.length} lines timed`}
          </span>
        </div>
      </section>
    )
  }
  return (
    <section className="stage-section sync-box" aria-live="polite">
      <h2>Tap to sync</h2>
      {next ? (
        <>
          <p className="small">
            {playing ? 'Press' : 'Start the song with Space, then press'} <Kbd>Enter</Kbd> on the first syllable of line{' '}
            {syncIndex + 1}:
          </p>
          <p className="ar sync-next">{next.arabic || '…'}</p>
        </>
      ) : (
        <p className="small">
          Every line has a start. Press <Kbd>Enter</Kbd> once more when the last line ends.
        </p>
      )}
      <div className="stage-row">
        <Button variant="primary" icon={<CornerDownLeft size={15} />} onClick={onTap}>
          {next ? 'Tap' : 'End of last line'}
        </Button>
        <Button icon={<Delete size={15} />} disabled={syncIndex === 0} onClick={onUndo} title="Undo the last tap (Backspace)">
          Undo
        </Button>
        <Button className="push" onClick={onDone} title="Leave sync mode (S or Esc)">
          Done
        </Button>
      </div>
    </section>
  )
}

const KEYS: [string, string][] = [
  ['Space', 'Play or pause'],
  ['↑ ↓', 'Previous or next line'],
  ['← →', 'Back or forward 3 seconds'],
  ['Enter', 'Play the line once (in sync mode: tap)'],
  ['L', 'Loop the line on or off'],
  ['− =', 'Slower or faster'],
  [', .', 'Move the line’s start earlier or later'],
  ['< >', 'Move the line’s end earlier or later'],
  ['S', 'Sync mode on or off'],
  ['R', 'Record a take on or off'],
  ['C', 'Original line, then your take'],
  ['Esc', 'Stop the loop, or leave a text field'],
]

export function ShortcutList({ extra = [] }: { extra?: [string, string][] }) {
  return (
    <details className="stage-section keys">
      <summary>Keyboard shortcuts</summary>
      <dl>
        {[...KEYS, ...extra].map(([key, what]) => (
          <div key={key}>
            <dt>
              {key.split(' ').map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

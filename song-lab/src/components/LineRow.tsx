import { memo } from 'react'
import { ArrowDown, ArrowUp, Minus, Play, Plus, Timer, Trash2 } from 'lucide-react'
import type { LinePatch } from '../api'
import { EditableText } from './EditableText'
import { Menu } from './Menu'
import { formatTime } from '../../shared/text'
import type { Line } from '../../shared/types'

export interface LineActions {
  saveLine: (id: string, patch: LinePatch) => void
  deleteLine: (line: Line) => void
  moveLine: (id: string, delta: -1 | 1) => void
  addLineAfter: (line: Line) => void
  /** Make this the highlighted line and move the song to it. */
  selectLine: (line: Line) => void
  /** Play the line once. */
  playLine: (line: Line) => void
  nudgeLine: (line: Line, edge: 'start' | 'end', direction: -1 | 1) => void
  syncFrom: (line: Line) => void
}

function Nudger({
  label,
  time,
  onNudge,
}: {
  label: string
  time: number | null
  onNudge: (direction: -1 | 1) => void
}) {
  return (
    <span className="nudger">
      <span className="nudger-label">{label}</span>
      <button type="button" aria-label={`${label} 0.25 seconds earlier`} title="0.25 s earlier" onClick={() => onNudge(-1)}>
        <Minus size={13} />
      </button>
      <span className="nudger-time">{formatTime(time, 2)}</span>
      <button type="button" aria-label={`${label} 0.25 seconds later`} title="0.25 s later" onClick={() => onNudge(1)}>
        <Plus size={13} />
      </button>
    </span>
  )
}

export const LineRow = memo(function LineRow({
  line,
  number,
  isFirst,
  isLast,
  isCurrent,
  isNextTap,
  endSec,
  actions,
}: {
  line: Line
  number: number
  isFirst: boolean
  isLast: boolean
  /** The highlighted line: the one being sung, looped or selected. */
  isCurrent: boolean
  /** In sync mode, the line the next tap will time. */
  isNextTap: boolean
  /** Where the line ends, worked out from the next line when it has no end of its own. */
  endSec: number | null
  actions: LineActions
}) {
  const timed = line.startSec != null
  return (
    <li
      className={`line${isCurrent ? ' is-current' : ''}${isNextTap ? ' is-next-tap' : ''}`}
      id={`line-${line.id}`}
      aria-current={isCurrent ? 'true' : undefined}
    >
      <button
        type="button"
        className="line-gutter"
        onClick={() => actions.selectLine(line)}
        title={timed ? 'Go to this line' : 'Select this line'}
        aria-label={`Line ${number}${timed ? `, at ${formatTime(line.startSec, 0)}` : ', not timed yet'}`}
      >
        <span className="line-num">{number}</span>
        <span className="line-time">{timed ? formatTime(line.startSec) : '·'}</span>
      </button>

      <div className="line-text">
        <EditableText
          rtl
          className="line-ar"
          value={line.arabic}
          label={`Line ${number}, Arabic`}
          placeholder="اكتب هنا"
          onSave={(arabic) => actions.saveLine(line.id, { arabic })}
        />
        <EditableText
          className="line-tr"
          value={line.translit}
          label={`Line ${number}, transliteration`}
          placeholder="transliteration"
          onSave={(translit) => actions.saveLine(line.id, { translit, source: 'user' })}
        />
        <EditableText
          className="line-gl"
          value={line.gloss}
          label={`Line ${number}, English`}
          placeholder="English"
          onSave={(gloss) => actions.saveLine(line.id, { gloss, source: 'user' })}
        />

        {isCurrent && (
          <div className="line-timing">
            {timed ? (
              <>
                <button type="button" className="chip-btn" onClick={() => actions.playLine(line)} title="Play this line once (Enter)">
                  <Play size={13} />
                  Play line
                </button>
                <Nudger label="Start" time={line.startSec} onNudge={(d) => actions.nudgeLine(line, 'start', d)} />
                <Nudger label="End" time={endSec} onNudge={(d) => actions.nudgeLine(line, 'end', d)} />
              </>
            ) : (
              <span className="muted small">Not timed yet. Use tap-to-sync to give this line a start.</span>
            )}
          </div>
        )}
      </div>

      <div className="line-actions">
        <Menu
          label={`More actions for line ${number}`}
          items={[
            { label: 'Sync from this line', icon: <Timer size={15} />, onSelect: () => actions.syncFrom(line) },
            { label: 'Add a line below', icon: <Plus size={15} />, onSelect: () => actions.addLineAfter(line) },
            { label: 'Move up', icon: <ArrowUp size={15} />, disabled: isFirst, onSelect: () => actions.moveLine(line.id, -1) },
            { label: 'Move down', icon: <ArrowDown size={15} />, disabled: isLast, onSelect: () => actions.moveLine(line.id, 1) },
            { label: 'Delete line', icon: <Trash2 size={15} />, danger: true, onSelect: () => actions.deleteLine(line) },
          ]}
        />
      </div>
    </li>
  )
})

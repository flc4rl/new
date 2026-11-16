import { memo } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import type { LinePatch } from '../api'
import { EditableText } from './EditableText'
import { Menu } from './Menu'
import type { Line } from '../../shared/types'

export interface LineActions {
  saveLine: (id: string, patch: LinePatch) => void
  deleteLine: (line: Line) => void
  moveLine: (id: string, delta: -1 | 1) => void
  addLineAfter: (line: Line) => void
}

export const LineRow = memo(function LineRow({
  line,
  number,
  isFirst,
  isLast,
  actions,
}: {
  line: Line
  number: number
  isFirst: boolean
  isLast: boolean
  actions: LineActions
}) {
  return (
    <li className="line" id={`line-${line.id}`}>
      <div className="line-gutter">
        <span className="line-num">{number}</span>
      </div>

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
      </div>

      <div className="line-actions">
        <Menu
          label={`More actions for line ${number}`}
          items={[
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

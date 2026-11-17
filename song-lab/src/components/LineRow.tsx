import { memo, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  CircleCheck,
  Layers,
  MessageCircleQuestion,
  Minus,
  Play,
  Plus,
  Rows3,
  Timer,
  Trash2,
} from 'lucide-react'
import type { LinePatch } from '../api'
import { EditableText } from './EditableText'
import { Menu } from './Menu'
import { WordsPanel, type WordActions } from './WordsPanel'
import { AskForm, QuestionList } from './Questions'
import { formatTime } from '../../shared/text'
import type { CardRef, Line, Question, Word } from '../../shared/types'

export interface LineActions extends WordActions {
  saveLine: (id: string, patch: LinePatch) => void
  deleteLine: (line: Line) => void
  moveLine: (id: string, delta: -1 | 1) => void
  addLineAfter: (line: Line) => void
  /** Make this the highlighted line and move the song to it. */
  selectLine: (line: Line) => void
  /** Play the line once. */
  playLine: (line: Line, onFinish?: () => void) => void
  nudgeLine: (line: Line, edge: 'start' | 'end', direction: -1 | 1) => void
  syncFrom: (line: Line) => void
  toggleWords: (lineId: string) => void
  makeLineCard: (line: Line) => void
  setLineStatus: (line: Line, status: Line['status']) => void
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
  words,
  cards,
  questions,
  wordsOpen,
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
  words: Word[]
  /** Review cards made from this line or its words. */
  cards: CardRef[]
  /** Questions about this line and its words. */
  questions: Question[]
  wordsOpen: boolean
  actions: LineActions
}) {
  const [asking, setAsking] = useState(false)
  const timed = line.startSec != null
  const hasLineCard = cards.some((c) => c.kind === 'line')
  const verified = line.status === 'verified'
  const claudeDraft = !verified && line.source === 'claude'
  const lineQuestions = questions.filter((q) => !q.wordId)
  const wordQuestions = questions.filter((q) => q.wordId && !q.resolved)
  const openWordQuestions = wordQuestions.length
  return (
    <li
      className={`line${isCurrent ? ' is-current' : ''}${isNextTap ? ' is-next-tap' : ''}${verified ? ' is-verified' : ''}${
        claudeDraft ? ' is-claude' : ''
      }`}
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

        {(claudeDraft || (line.unsure && !verified) || openWordQuestions > 0) && (
          <div className="line-tags">
            {claudeDraft && (
              <span className="tag tag-draft" title="Drafted by Claude. Check it, or ask your tutor, then mark it verified.">
                Claude draft
              </span>
            )}
            {line.unsure && !verified && (
              <span className="tag tag-unsure" title="Claude was not sure about this line">
                Claude unsure
              </span>
            )}
            {openWordQuestions > 0 && !wordsOpen && (
              <button type="button" className="tag tag-question" onClick={() => actions.toggleWords(line.id)}>
                {openWordQuestions} {openWordQuestions === 1 ? 'question' : 'questions'} on words
              </button>
            )}
          </div>
        )}
        {line.note && (
          <p className="line-note ltr" dir="auto">
            {line.note}
          </p>
        )}
        <QuestionList questions={lineQuestions} actions={actions} />
        {asking && (
          <AskForm
            placeholder="Question for the tutor about this line"
            onAsk={(text) => actions.addQuestion({ lineId: line.id }, text)}
            onClose={() => setAsking(false)}
          />
        )}

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

        {wordsOpen && <WordsPanel line={line} words={words} cards={cards} questions={wordQuestions} actions={actions} />}
      </div>

      <div className="line-actions">
        <button
          type="button"
          className={`btn btn-ghost btn-sm btn-icon status-icon${verified ? ' is-verified' : ''}`}
          aria-pressed={verified}
          aria-label={verified ? `Line ${number} is verified` : `Mark line ${number} as verified`}
          title={verified ? 'Verified. Click to set it back to draft.' : 'Draft. Click to mark it verified, once checked.'}
          onClick={() => actions.setLineStatus(line, verified ? 'draft' : 'verified')}
        >
          <CircleCheck size={16} />
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm btn-icon"
          aria-pressed={wordsOpen}
          aria-label={`Words of line ${number}`}
          title={wordsOpen ? 'Hide the words' : words.length ? `Show the ${words.length} words` : 'Split into words'}
          onClick={() => actions.toggleWords(line.id)}
        >
          <Rows3 size={16} />
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm btn-icon"
          aria-pressed={hasLineCard}
          aria-label={hasLineCard ? `Line ${number} is in the deck` : `Make a card from line ${number}`}
          title={hasLineCard ? 'This line is in the deck' : 'Make a card from this line'}
          onClick={() => !hasLineCard && actions.makeLineCard(line)}
        >
          <Layers size={16} />
        </button>
        <Menu
          label={`More actions for line ${number}`}
          items={[
            { label: 'Ask the tutor about this line', icon: <MessageCircleQuestion size={15} />, onSelect: () => setAsking(true) },
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

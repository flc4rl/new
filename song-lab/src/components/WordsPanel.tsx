import { memo, useState, type FormEvent } from 'react'
import { Layers, Plus, RefreshCw, Trash2 } from 'lucide-react'
import type { WordPatch } from '../api'
import { CommitInput } from './CommitInput'
import { REGISTERS, type CardRef, type Line, type Register, type Word } from '../../shared/types'

export interface WordActions {
  splitLine: (lineId: string) => void
  addWord: (lineId: string, arabic: string) => void
  saveWord: (id: string, patch: WordPatch) => void
  deleteWord: (word: Word) => void
  makeWordCard: (word: Word) => void
}

const REGISTER_HINT: Record<Register, string> = {
  everyday: 'Used in ordinary speech',
  poetic: 'Poetic or older; check before using it in conversation',
  unknown: 'Not decided yet',
}

function WordEditor({ word, hasCard, actions }: { word: Word; hasCard: boolean; actions: WordActions }) {
  const save = (patch: WordPatch) => actions.saveWord(word.id, patch)
  return (
    <div className="word-editor">
      <label className="field word-field-ar">
        <span>Arabic</span>
        <CommitInput rtl value={word.arabic} label="Word, Arabic" onCommit={(arabic) => arabic && save({ arabic })} />
      </label>
      <label className="field">
        <span>Transliteration</span>
        <CommitInput value={word.translit} label="Word, transliteration" onCommit={(translit) => save({ translit })} />
      </label>
      <label className="field">
        <span>Meaning</span>
        <CommitInput value={word.gloss} label="Word, meaning" onCommit={(gloss) => save({ gloss })} />
      </label>
      <label className="field word-field-root">
        <span>Root</span>
        <CommitInput rtl value={word.root} label="Word, root" onCommit={(root) => save({ root })} />
      </label>
      <div className="field word-field-reg">
        <span>Register</span>
        <div className="segmented" role="group" aria-label="Register">
          {REGISTERS.map((register) => (
            <button
              key={register}
              type="button"
              className={`reg-${register}`}
              aria-pressed={word.register === register}
              title={REGISTER_HINT[register]}
              onClick={() => save({ register })}
            >
              {register}
            </button>
          ))}
        </div>
      </div>
      <label className="field word-field-note">
        <span>Note</span>
        <CommitInput value={word.note} label="Word, note" placeholder="optional" onCommit={(note) => save({ note })} />
      </label>
      <div className="word-editor-actions">
        <button type="button" className="chip-btn" disabled={hasCard} onClick={() => actions.makeWordCard(word)}>
          <Layers size={13} />
          {hasCard ? 'In the deck' : 'Make a card'}
        </button>
        <button type="button" className="chip-btn is-danger" onClick={() => actions.deleteWord(word)}>
          <Trash2 size={13} />
          Delete word
        </button>
      </div>
    </div>
  )
}

export const WordsPanel = memo(function WordsPanel({
  line,
  words,
  cards,
  actions,
}: {
  line: Line
  words: Word[]
  cards: CardRef[]
  actions: WordActions
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const selected = words.find((w) => w.id === selectedId) ?? null
  const inDeck = (word: Word) => cards.some((c) => c.kind === 'word' && c.wordId === word.id)

  if (words.length === 0) {
    return (
      <div className="words words-empty">
        <button type="button" className="chip-btn" disabled={!line.arabic} onClick={() => actions.splitLine(line.id)}>
          Split into words
        </button>
        <span className="muted small">Each word then gets its own transliteration, meaning and register.</span>
      </div>
    )
  }

  function submitNew(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const input = e.currentTarget.elements.namedItem('arabic') as HTMLInputElement
    const arabic = input.value.trim()
    if (arabic) actions.addWord(line.id, arabic)
    setAdding(false)
  }

  return (
    <div className="words">
      {/* The chips run right to left, in the order the words are sung. */}
      <div className="word-chips" dir="rtl">
        {words.map((word) => {
          const hasCard = inDeck(word)
          return (
            <div
              key={word.id}
              className={`word-chip reg-${word.register}${word.status === 'verified' ? ' is-verified' : ''}${
                word.id === selectedId ? ' is-selected' : ''
              }`}
            >
              <button
                type="button"
                className="word-chip-main"
                aria-expanded={word.id === selectedId}
                title={`${REGISTER_HINT[word.register]}. Click to edit.`}
                onClick={() => setSelectedId(word.id === selectedId ? null : word.id)}
              >
                <span className="ar word-ar">{word.arabic}</span>
                <span className="ltr word-tr">{word.translit || '·'}</span>
                <span className="ltr word-gl">{word.gloss || '·'}</span>
              </button>
              <button
                type="button"
                className="word-chip-card"
                aria-pressed={hasCard}
                aria-label={hasCard ? `${word.arabic} is in the deck` : `Make a card from ${word.arabic}`}
                title={hasCard ? 'In the deck' : 'Make a card'}
                onClick={() => !hasCard && actions.makeWordCard(word)}
              >
                <Layers size={12} />
              </button>
            </div>
          )
        })}
      </div>

      {selected && <WordEditor key={selected.id} word={selected} hasCard={inDeck(selected)} actions={actions} />}

      <div className="words-foot">
        {adding ? (
          <form className="word-add" onSubmit={submitNew}>
            <input
              name="arabic"
              className="input ar"
              dir="rtl"
              lang="ar"
              aria-label="New word, Arabic"
              autoFocus
              onBlur={(e) => !e.currentTarget.value && setAdding(false)}
              onKeyDown={(e) => e.key === 'Escape' && setAdding(false)}
            />
            <button type="submit" className="chip-btn">
              Add
            </button>
          </form>
        ) : (
          <button type="button" className="chip-btn" onClick={() => setAdding(true)}>
            <Plus size={13} />
            Add a word
          </button>
        )}
        <button
          type="button"
          className="chip-btn"
          title="Split the line again. Words that still match keep their details."
          onClick={() => actions.splitLine(line.id)}
        >
          <RefreshCw size={13} />
          Split again
        </button>
        <span className="legend">
          <span className="legend-item reg-everyday">everyday</span>
          <span className="legend-item reg-poetic">poetic</span>
          <span className="legend-item reg-unknown">unknown</span>
        </span>
      </div>
    </div>
  )
})

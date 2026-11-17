import { useState, type FormEvent } from 'react'
import { Check, MessageCircleQuestion, X } from 'lucide-react'
import type { Question } from '../../shared/types'

export interface QuestionActions {
  addQuestion: (target: { lineId?: string; wordId?: string }, text: string) => void
  resolveQuestion: (question: Question) => void
  deleteQuestion: (question: Question) => void
}

/** Open questions for the tutor, each with a way to mark it answered. */
export function QuestionList({ questions, actions }: { questions: Question[]; actions: QuestionActions }) {
  const open = questions.filter((q) => !q.resolved)
  if (open.length === 0) return null
  return (
    <ul className="questions">
      {open.map((q) => (
        <li key={q.id} className="question">
          <MessageCircleQuestion size={15} aria-hidden="true" />
          <span className="question-text" dir="auto">
            {q.text}
          </span>
          <button type="button" className="icon-btn" title="Answered: take it off the tutor queue" aria-label="Mark answered" onClick={() => actions.resolveQuestion(q)}>
            <Check size={14} />
          </button>
          <button type="button" className="icon-btn" title="Delete the question" aria-label="Delete question" onClick={() => actions.deleteQuestion(q)}>
            <X size={14} />
          </button>
        </li>
      ))}
    </ul>
  )
}

/** A one-line form for a new question. */
export function AskForm({
  placeholder,
  onAsk,
  onClose,
}: {
  placeholder: string
  onAsk: (text: string) => void
  onClose: () => void
}) {
  const [text, setText] = useState('')
  function submit(e: FormEvent) {
    e.preventDefault()
    if (text.trim()) onAsk(text.trim())
    onClose()
  }
  return (
    <form className="ask" onSubmit={submit}>
      <input
        className="input"
        dir="auto"
        value={text}
        placeholder={placeholder}
        aria-label="Question for the tutor"
        autoFocus
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      />
      <button type="submit" className="chip-btn" disabled={!text.trim()}>
        Add question
      </button>
      <button type="button" className="chip-btn" onClick={onClose}>
        Cancel
      </button>
    </form>
  )
}

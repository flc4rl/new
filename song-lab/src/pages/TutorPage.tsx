import { useState } from 'react'
import { ArrowLeft, CircleCheck, Download, MessageCircleQuestion, Printer } from 'lucide-react'
import { api } from '../api'
import { useLoad } from '../lib/hooks'
import { Button, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { EditableText } from '../components/EditableText'
import { CommitInput } from '../components/CommitInput'
import { AskForm, QuestionList, type QuestionActions } from '../components/Questions'
import { formatTime } from '../../shared/text'
import { watchUrl } from '../../shared/youtube'
import { REGISTERS, type Line, type TutorLineItem, type TutorSongGroup, type Word } from '../../shared/types'

function counts(groups: TutorSongGroup[]) {
  let lines = 0
  let words = 0
  let questions = 0
  for (const item of groups.flatMap((g) => g.lines)) {
    if (item.lineIsDraft) lines++
    words += item.words.filter((w) => w.word.status === 'draft').length
    questions += item.questions.length + item.words.reduce((n, w) => n + w.questions.length, 0)
  }
  return { lines, words, questions }
}

function Summary({ groups }: { groups: TutorSongGroup[] }) {
  const c = counts(groups)
  return (
    <p className="muted">
      {c.questions} open {c.questions === 1 ? 'question' : 'questions'}, {c.lines} draft {c.lines === 1 ? 'line' : 'lines'}{' '}
      and {c.words} draft {c.words === 1 ? 'word' : 'words'}
      {groups.length > 1 ? `, in ${groups.length} songs` : ''}.
    </p>
  )
}

export function TutorPage({ print }: { print: boolean }) {
  const queue = useLoad(api.tutor, [])
  if (queue.error) return <LoadError error={queue.error} onRetry={queue.reload} />
  if (!queue.data) return <Spinner />
  return print ? <PrintView groups={queue.data} /> : <TutorQueue groups={queue.data} reload={queue.reload} />
}

function TutorQueue({ groups, reload }: { groups: TutorSongGroup[]; reload: () => Promise<void> }) {
  const toast = useToast()
  const run = (p: Promise<unknown>, after?: string) =>
    p
      .then(() => {
        if (after) toast.info(after)
        return reload()
      })
      .catch(toast.error)

  const questionActions: QuestionActions = {
    addQuestion: (target, text) => void run(api.addQuestion({ ...target, text })),
    resolveQuestion: (q) => void run(api.updateQuestion(q.id, { resolved: true })),
    deleteQuestion: (q) => void run(api.deleteQuestion(q.id)),
  }

  return (
    <div className="page tutor">
      <div className="page-head">
        <div>
          <h1>Tutor queue</h1>
          {groups.length > 0 && <Summary groups={groups} />}
        </div>
        {groups.length > 0 && (
          <div className="page-head-actions">
            <a className="btn btn-default btn-md" href={api.tutorMarkdownUrl} download>
              <Download size={16} />
              <span>Markdown</span>
            </a>
            <a className="btn btn-primary btn-md" href="#/tutor/print">
              <Printer size={16} />
              <span>Printable page</span>
            </a>
          </div>
        )}
      </div>

      {groups.length === 0 ? (
        <EmptyState title="Nothing to check">
          <p>
            Every line and word is verified and every question is answered. Draft lines, draft words and questions
            for the tutor appear here, grouped by song.
          </p>
        </EmptyState>
      ) : (
        groups.map((group) => (
          <section key={group.song.id} className="tutor-song">
            <h2>
              <a href={`#/song/${group.song.id}`} dir="auto">
                {group.song.title}
              </a>
              {group.song.artist && (
                <span className="muted" dir="auto">
                  {' '}
                  · {group.song.artist}
                </span>
              )}
            </h2>
            {group.lines.map((item) => (
              <TutorLine
                key={item.line.id}
                item={item}
                youtubeId={group.song.youtubeId}
                questionActions={questionActions}
                onLine={(patch, after) => run(api.updateLine(item.line.id, patch), after)}
                onWord={(word, patch, after) => run(api.updateWord(word.id, patch), after)}
              />
            ))}
          </section>
        ))
      )}
    </div>
  )
}

function TutorLine({
  item,
  youtubeId,
  questionActions,
  onLine,
  onWord,
}: {
  item: TutorLineItem
  youtubeId: string
  questionActions: QuestionActions
  onLine: (patch: Partial<Pick<Line, 'arabic' | 'translit' | 'gloss' | 'status'>>, after?: string) => void
  onWord: (word: Word, patch: Partial<Pick<Word, 'arabic' | 'translit' | 'gloss' | 'register' | 'status'>>, after?: string) => void
}) {
  const [asking, setAsking] = useState(false)
  const { line } = item
  const number = line.order + 1
  return (
    <article className="tutor-line">
      <header className="tutor-line-head">
        <span className="tutor-line-num">Line {number}</span>
        {line.startSec != null && (
          <a href={watchUrl(youtubeId, line.startSec)} target="_blank" rel="noreferrer" className="small">
            {formatTime(line.startSec, 0)} on YouTube
          </a>
        )}
        {item.lineIsDraft ? (
          <span className="tag tag-draft">{line.source === 'claude' ? 'Claude draft' : line.source === 'google' ? 'Google draft' : 'draft'}</span>
        ) : (
          <span className="tag tag-verified">verified</span>
        )}
        {line.unsure && item.lineIsDraft && <span className="tag tag-unsure">Claude unsure</span>}
        <span className="push" />
        {item.lineIsDraft && (
          <Button size="sm" icon={<CircleCheck size={15} />} onClick={() => onLine({ status: 'verified' }, `Line ${number} verified.`)}>
            Mark line verified
          </Button>
        )}
      </header>

      <div className="tutor-line-text">
        <EditableText rtl className="line-ar" value={line.arabic} label={`Line ${number}, Arabic`} onSave={(arabic) => onLine({ arabic })} />
        <EditableText className="line-tr" value={line.translit} label={`Line ${number}, transliteration`} placeholder="transliteration" onSave={(translit) => onLine({ translit })} />
        <EditableText className="line-gl" value={line.gloss} label={`Line ${number}, English`} placeholder="English" onSave={(gloss) => onLine({ gloss })} />
        {line.note && (
          <p className="line-note" dir="auto">
            {line.note}
          </p>
        )}
      </div>

      <QuestionList questions={item.questions} actions={questionActions} />
      {asking ? (
        <AskForm
          placeholder="Question for the tutor about this line"
          onAsk={(text) => questionActions.addQuestion({ lineId: line.id }, text)}
          onClose={() => setAsking(false)}
        />
      ) : (
        <button type="button" className="chip-btn tutor-ask" onClick={() => setAsking(true)}>
          <MessageCircleQuestion size={13} />
          Add a question
        </button>
      )}

      {item.words.length > 0 && (
        <table className="tutor-words">
          <thead>
            <tr>
              <th>Word</th>
              <th>Transliteration</th>
              <th>Meaning</th>
              <th>Register</th>
              <th>
                <span className="sr-only">Status</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {item.words.map(({ word, questions }) => (
              <tr key={word.id}>
                <td>
                  <span className="ar tutor-word-ar" lang="ar">
                    {word.arabic}
                  </span>
                  {word.unsure && word.status === 'draft' && <span className="tag tag-unsure">unsure</span>}
                </td>
                <td>
                  <CommitInput value={word.translit} label={`${word.arabic}, transliteration`} onCommit={(translit) => onWord(word, { translit })} />
                </td>
                <td>
                  <CommitInput value={word.gloss} label={`${word.arabic}, meaning`} onCommit={(gloss) => onWord(word, { gloss })} />
                  <QuestionList questions={questions} actions={questionActions} />
                </td>
                <td>
                  <select
                    className="input"
                    value={word.register}
                    aria-label={`${word.arabic}, register`}
                    onChange={(e) => onWord(word, { register: e.target.value as Word['register'] })}
                  >
                    {REGISTERS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {word.status === 'draft' ? (
                    <Button size="sm" variant="ghost" icon={<CircleCheck size={15} />} onClick={() => onWord(word, { status: 'verified' }, `${word.arabic} verified.`)}>
                      Verify
                    </Button>
                  ) : (
                    <span className="tag tag-verified">verified</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  )
}

/** A clean page to print or save as PDF before a lesson. */
function PrintView({ groups }: { groups: TutorSongGroup[] }) {
  const date = new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
  return (
    <div className="page print-page">
      <div className="print-controls">
        <a className="btn btn-ghost btn-md" href="#/tutor">
          <ArrowLeft size={16} />
          <span>Back to the queue</span>
        </a>
        <Button variant="primary" icon={<Printer size={16} />} onClick={() => window.print()}>
          Print
        </Button>
      </div>
      <header>
        <h1>For my Sudanese Arabic lesson</h1>
        <p className="muted">Questions and drafts to check, {date}.</p>
        {groups.length > 0 && <Summary groups={groups} />}
        <p className="print-key small">
          Transliteration: capitals mark emphatic consonants and the deep h (S D T Z H); 3 is ع and ' is the glottal stop.
        </p>
      </header>
      {groups.length === 0 && <p>Nothing to check: every line and word is verified and every question is answered.</p>}
      {groups.map((group) => (
        <section key={group.song.id} className="print-song">
          <h2 dir="auto">
            {group.song.title}
            {group.song.artist ? ` · ${group.song.artist}` : ''}
          </h2>
          <p className="small muted">{watchUrl(group.song.youtubeId)}</p>
          {group.lines.map((item) => (
            <article key={item.line.id} className="print-line">
              <h3>
                Line {item.line.order + 1}
                {item.line.startSec != null ? ` · ${formatTime(item.line.startSec, 0)}` : ''}
                {item.lineIsDraft ? ` · ${item.line.source === 'claude' ? 'Claude draft' : item.line.source === 'google' ? 'Google draft' : 'draft'}` : ''}
                {item.lineIsDraft && item.line.unsure ? ' · Claude unsure' : ''}
              </h3>
              <p className="ar print-ar" lang="ar">
                {item.line.arabic}
              </p>
              <p className="ltr print-tr">{item.line.translit || '(no transliteration)'}</p>
              <p className="ltr print-gl">{item.line.gloss || '(no English)'}</p>
              {item.line.note && <p className="small">{item.line.note}</p>}
              {item.questions.map((q) => (
                <p key={q.id} className="print-q" dir="auto">
                  <b>Question:</b> {q.text}
                </p>
              ))}
              {item.words.length > 0 && (
                <table className="print-words">
                  <tbody>
                    {item.words.map(({ word, questions }) => (
                      <tr key={word.id}>
                        <td className="ar" lang="ar">
                          {word.arabic}
                        </td>
                        <td className="ltr">{word.translit || '(none)'}</td>
                        <td className="ltr">
                          {word.gloss || '(none)'}
                          {questions.map((q) => (
                            <div key={q.id} className="print-q" dir="auto">
                              <b>Question:</b> {q.text}
                            </div>
                          ))}
                        </td>
                        <td>
                          {word.register}
                          {word.unsure ? ', unsure' : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}

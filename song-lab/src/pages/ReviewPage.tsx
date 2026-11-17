import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, Library, Plus, RotateCcw, Volume2 } from 'lucide-react'
import { api } from '../api'
import { useApp } from '../App'
import { useHotkeys, useLoad, usePlayer } from '../lib/hooks'
import { Button, EmptyState, Kbd, LoadError, Spinner, useToast } from '../components/ui'
import { AddPhraseDialog } from '../components/AddPhraseDialog'
import { previewInterval } from '../../shared/srs'
import { watchUrl } from '../../shared/youtube'
import { GRADES, type CardView, type Grade, type ReviewQueue } from '../../shared/types'

/**
 * What the learner sees of the card so far.
 * listen: the clip plays and the text is hidden.
 * front:  Arabic and transliteration (or, English to Arabic, the English).
 * back:   everything.
 */
type Stage = 'listen' | 'front' | 'back'

const GRADE_LABEL: Record<Grade, string> = { again: 'Again', hard: 'Hard', good: 'Good', easy: 'Easy' }

export function ReviewPage() {
  const queue = useLoad(api.queue, [])
  const [adding, setAdding] = useState(false)
  // Each visit to the page starts a fresh session from the server's queue.
  if (queue.error) return <LoadError error={queue.error} onRetry={queue.reload} />
  if (!queue.data) return <Spinner />
  return (
    <>
      <ReviewSession key={queue.data.cards.map((c) => c.id).join()} initial={queue.data} onAddPhrase={() => setAdding(true)} />
      {adding && (
        <AddPhraseDialog
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false)
            void queue.reload()
          }}
        />
      )}
    </>
  )
}

function ReviewSession({ initial, onAddPhrase }: { initial: ReviewQueue; onAddPhrase: () => void }) {
  const toast = useToast()
  const { settings, refreshCounts } = useApp()
  const [queue, setQueue] = useState<CardView[]>(initial.cards)
  const [stage, setStage] = useState<Stage>('front')
  const [done, setDone] = useState(0)
  const [again, setAgain] = useState(0)
  const [busy, setBusy] = useState(false)
  const card = queue[0] ?? null

  // One player for the whole session. It starts on the first video with a clip and switches as cards need.
  const firstVideo = useMemo(() => initial.cards.find((c) => c.clip)?.clip?.youtubeId ?? null, [initial])
  const firstRate = useMemo(() => initial.cards.find((c) => c.clip)?.clip?.rate ?? 1, [initial])
  const { hostRef, player, snap } = usePlayer(firstVideo, firstRate)
  const ready = snap.status === 'ready'
  const clipBlocked = !!card?.clip && snap.status === 'error'

  const playing = useRef<string | null>(null)

  const playClip = useCallback(
    (target: CardView) => {
      const clip = target.clip
      if (!clip || !player || player.getSnapshot().status !== 'ready') return false
      playing.current = target.id
      player.load(clip.youtubeId, clip.rate, Math.max(0, clip.startSec - settings.loopLeadInSec))
      player.playClip(clip.startSec, clip.endSec, settings.loopLeadInSec, () => {
        // After listening, show the words.
        if (playing.current === target.id) setStage((s) => (s === 'listen' ? 'front' : s))
      })
      return true
    },
    [player, settings.loopLeadInSec],
  )

  // A new card: listen first when it has a clip and is shown Arabic side first.
  const shownId = useRef<string | null>(null)
  useEffect(() => {
    if (!card) {
      player?.stopLoop()
      player?.pause()
      return
    }
    if (shownId.current === card.id) return
    if (card.clip && !ready && snap.status !== 'error' && card.direction === 'ar-en') {
      // Wait for the player before deciding.
      setStage('listen')
      return
    }
    shownId.current = card.id
    player?.stopLoop()
    if (card.direction === 'ar-en' && card.clip && ready) {
      setStage('listen')
      playClip(card)
    } else {
      player?.pause()
      setStage('front')
    }
  }, [card, ready, snap.status, player, playClip])

  // If the clip cannot play here, do not leave the learner waiting on it.
  useEffect(() => {
    if (clipBlocked && stage === 'listen') setStage('front')
  }, [clipBlocked, stage])

  const reveal = useCallback(() => {
    if (!card) return
    if (stage === 'listen') {
      setStage('front')
      return
    }
    if (stage === 'front') {
      setStage('back')
      // English to Arabic: hear the line once the answer is shown.
      if (card.direction === 'en-ar' && card.clip) playClip(card)
    }
  }, [card, stage, playClip])

  const grade = useCallback(
    async (g: Grade) => {
      if (!card || stage !== 'back' || busy) return
      setBusy(true)
      try {
        const result = await api.grade(card.id, g)
        player?.stopLoop()
        setDone((n) => n + 1)
        if (g === 'again') setAgain((n) => n + 1)
        setQueue((q) => {
          const rest = q.slice(1)
          // A card that has to come back later in the session goes to the end of the queue.
          return result.requeue ? [...rest, result.card] : rest
        })
        shownId.current = null
        refreshCounts()
      } catch (err) {
        toast.error(err)
      } finally {
        setBusy(false)
      }
    },
    [card, stage, busy, player, refreshCounts, toast],
  )

  useHotkeys({
    ' ': () => (stage === 'back' ? undefined : reveal()),
    Enter: () => (stage === 'back' ? undefined : reveal()),
    r: () => card && playClip(card),
    '1': () => void grade('again'),
    '2': () => void grade('hard'),
    '3': () => void grade('good'),
    '4': () => void grade('easy'),
  })

  const now = new Date()
  const remaining = queue.length

  return (
    <div className="page page-narrow review">
      <div className="page-head">
        <h1>Review</h1>
        <div className="page-head-actions">
          <Button icon={<Plus size={16} />} onClick={onAddPhrase}>
            Add a phrase
          </Button>
          <a className="btn btn-default btn-md" href="#/deck">
            <Library size={16} />
            <span>All cards</span>
          </a>
        </div>
      </div>

      {card && (
        <div className="review-progress" aria-live="polite">
          <span>
            <b>{remaining}</b> to go
          </span>
          <span>
            <b>{done}</b> done{again ? ` · ${again} again` : ''}
          </span>
          {card.isNew && <span className="tag tag-new">new card</span>}
        </div>
      )}

      {/* The player stays mounted for the whole session, and is only shown for cards with a clip. */}
      <div className={`review-video${card?.clip ? '' : ' is-hidden'}`}>
        {firstVideo && (
          <div className="player-frame player-small">
            <div ref={hostRef} className="player-host" />
            {snap.status === 'error' && card?.clip && (
              <div className="player-cover" role="alert">
                <p>{snap.error?.message}</p>
                <a className="btn btn-primary btn-sm" href={watchUrl(card.clip.youtubeId, card.clip.startSec)} target="_blank" rel="noreferrer">
                  <ExternalLink size={14} />
                  <span>Open on YouTube</span>
                </a>
              </div>
            )}
          </div>
        )}
      </div>

      {!card ? (
        <EmptyState
          title={done > 0 ? 'Done for now' : initial.totalCards === 0 ? 'The deck is empty' : 'Nothing to review right now'}
          action={
            initial.totalCards === 0 ? (
              <>
                <a className="btn btn-primary btn-md" href="#/">
                  <span>Go to the library</span>
                </a>
                <Button icon={<Plus size={16} />} onClick={onAddPhrase}>
                  Add a phrase
                </Button>
              </>
            ) : undefined
          }
        >
          {done > 0 ? (
            <p>
              You reviewed {done} {done === 1 ? 'card' : 'cards'}
              {again ? `, and went over ${again} again` : ''}. Cards come back on the day they are due.
            </p>
          ) : initial.totalCards === 0 ? (
            <p>
              Open a song and click the card button next to a line or a word, or add a phrase from a lesson or the
              coursebook.
            </p>
          ) : (
            <p>
              No card is due, and today’s {settings.newPerDay} new {settings.newPerDay === 1 ? 'card is' : 'cards are'}{' '}
              used up. Come back tomorrow, or change the daily limit under All cards.
            </p>
          )}
        </EmptyState>
      ) : (
        <article className={`flashcard is-${stage}`} aria-label="Card">
          <header className="flashcard-meta">
            <span>{card.direction === 'ar-en' ? 'Arabic → English' : 'English → Arabic'}</span>
            <span>{card.kind === 'phrase' ? 'phrase' : card.kind}</span>
            {card.songTitle && <span dir="auto">{card.songTitle}</span>}
            {card.status === 'draft' && (
              <span className="tag tag-draft" title="Not yet confirmed by you or your tutor">
                draft
              </span>
            )}
          </header>

          {card.direction === 'ar-en' ? (
            <>
              {stage === 'listen' ? (
                <div className="flashcard-listen">
                  <Volume2 size={28} />
                  <p>{ready ? 'Listen…' : 'Loading the clip…'}</p>
                </div>
              ) : (
                <div className="flashcard-face">
                  <p className="ar flashcard-ar" lang="ar">
                    {card.arabic}
                  </p>
                  {card.translit && <p className="ltr flashcard-tr">{card.translit}</p>}
                </div>
              )}
              {stage === 'back' && (
                <div className="flashcard-answer">
                  <p className="ltr flashcard-gl">{card.gloss || '(no meaning yet)'}</p>
                  {card.kind === 'word' && card.lineArabic && (
                    <p className="ar flashcard-context" lang="ar">
                      {card.lineArabic}
                    </p>
                  )}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="flashcard-face">
                <p className="ltr flashcard-prompt">{card.gloss || '(no meaning yet)'}</p>
                {stage !== 'back' && <p className="muted small">Say it in Sudanese Arabic, then reveal.</p>}
              </div>
              {stage === 'back' && (
                <div className="flashcard-answer">
                  <p className="ar flashcard-ar" lang="ar">
                    {card.arabic}
                  </p>
                  {card.translit && <p className="ltr flashcard-tr">{card.translit}</p>}
                  {card.kind === 'word' && card.lineArabic && (
                    <p className="ar flashcard-context" lang="ar">
                      {card.lineArabic}
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          <footer className="flashcard-actions">
            {card.clip && (
              <Button
                variant="ghost"
                icon={<RotateCcw size={15} />}
                disabled={!ready}
                onClick={() => playClip(card)}
                title="Play the clip again (R)"
              >
                Replay
              </Button>
            )}
            {stage !== 'back' ? (
              <Button variant="primary" className="flashcard-reveal" onClick={reveal}>
                {stage === 'listen' ? 'Show the words' : 'Show the answer'} <Kbd>Space</Kbd>
              </Button>
            ) : (
              <div className="grades" role="group" aria-label="How well did you know it?">
                {GRADES.map((g, i) => (
                  <button
                    key={g}
                    type="button"
                    className={`grade grade-${g}`}
                    disabled={busy}
                    onClick={() => void grade(g)}
                  >
                    <span className="grade-label">{GRADE_LABEL[g]}</span>
                    <span className="grade-when">{previewInterval(card, g, now)}</span>
                    <Kbd>{i + 1}</Kbd>
                  </button>
                ))}
              </div>
            )}
          </footer>
        </article>
      )}
    </div>
  )
}

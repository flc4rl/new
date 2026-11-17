import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ClipboardPaste, Eraser, Pencil, Rows3, Sparkles, SplitSquareHorizontal } from 'lucide-react'
import { api, type LinePatch, type NewCard } from '../api'
import { useApp } from '../App'
import { useHotkeys, useLoad, usePlayer } from '../lib/hooks'
import { activeLineAt, lineEnd, nudge, NUDGE_SEC, round2 } from '../lib/timing'
import { Button, Confirm, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { PlayerFrame, Transport } from '../components/PlayerPanel'
import { LoopPanel, ShortcutList, SyncPanel } from '../components/StagePanels'
import { EditSongDialog } from '../components/SongDialogs'
import { LineRow, type LineActions } from '../components/LineRow'
import { Menu } from '../components/Menu'
import { PasteLyricsDialog } from '../components/PasteLyricsDialog'
import { DraftDialog } from '../components/DraftDialog'
import { ShadowPanel, type ShadowControls } from '../components/ShadowPanel'
import type { CardRef, Line, Question, SongDetail, Word } from '../../shared/types'

type SetDetail = (next: SongDetail | ((prev: SongDetail) => SongDetail)) => void

const NO_WORDS: Word[] = []
const NO_CARDS: CardRef[] = []
const NO_QUESTIONS: Question[] = []

/** How much of a line is played back after a nudge, so the new edge can be heard. */
const NUDGE_PREVIEW_SEC = 2.5
const SEEK_STEP_SEC = 3

export function SongPage({ songId }: { songId: string }) {
  const detail = useLoad(() => api.song(songId), [songId])
  if (detail.error) {
    return (
      <div className="page">
        <LoadError error={detail.error} onRetry={detail.reload} />
        <a href="#/">Back to the library</a>
      </div>
    )
  }
  if (!detail.data) return <Spinner />
  return <SongView detail={detail.data} setDetail={detail.setData} reload={detail.reload} />
}

function SongView({ detail, setDetail, reload }: { detail: SongDetail; setDetail: SetDetail; reload: () => Promise<void> }) {
  const { song, lines } = detail
  const toast = useToast()
  const { meta, settings, saveSettings, refreshCounts } = useApp()
  const { hostRef, player, snap, retry } = usePlayer(song.youtubeId, song.savedRate)

  const [editing, setEditing] = useState(false)
  const [pasting, setPasting] = useState(false)
  const [deleting, setDeleting] = useState<Line | null>(null)
  const [clearing, setClearing] = useState(false)
  const [drafting, setDrafting] = useState(false)
  /** Lines Claude is translating in the background, after a paste. */
  const [translating, setTranslating] = useState(0)

  /** Translate new lines straight away, when a key is set and the setting is on. */
  const autoTranslate = (lineIds: string[]) => {
    if (!meta.claude || !settings.autoDraft || lineIds.length === 0) return
    setTranslating((n) => n + lineIds.length)
    api
      .draft(song.id, lineIds)
      .then((result) => {
        void reload()
        toast.info(
          `Claude translated ${result.updatedLines} ${result.updatedLines === 1 ? 'line' : 'lines'}. ` +
            'They are drafts: check them, then mark them verified.',
        )
      })
      .catch(toast.error)
      .finally(() => setTranslating((n) => Math.max(0, n - lineIds.length)))
  }

  /** The highlighted line: the one being sung, looped or picked by hand. */
  const [cursorId, setCursorId] = useState<string | null>(null)
  /** The line a loop or a one-off play is holding on to. */
  const [spanLineId, setSpanLineId] = useState<string | null>(null)
  const [looping, setLooping] = useState(false)
  const [syncing, setSyncing] = useState(false)
  /** In sync mode: the line the next tap will time. Equal to lines.length when only the final end is missing. */
  const [syncIndex, setSyncIndex] = useState(0)
  /** Lines whose word breakdown is open. */
  const [openWords, setOpenWords] = useState<ReadonlySet<string>>(new Set())
  const [allWordsOpen, setAllWordsOpen] = useState(false)

  // The newest state, for handlers that must not be recreated on every change.
  const live = useRef({ detail, snap, player, settings, cursorId, spanLineId, looping, syncing, syncIndex, openWords, allWordsOpen })
  live.current = { detail, snap, player, settings, cursorId, spanLineId, looping, syncing, syncIndex, openWords, allWordsOpen }

  const focusLine = useRef<string | null>(null)
  useEffect(() => {
    if (!focusLine.current) return
    document.querySelector<HTMLTextAreaElement>(`#line-${focusLine.current} .line-ar`)?.focus()
    focusLine.current = null
  }, [lines])

  // Remember the last speed used for this song.
  useEffect(() => {
    if (snap.status !== 'ready' || snap.rate === song.savedRate) return
    api
      .updateSong(song.id, { savedRate: snap.rate })
      .then((saved) => setDetail((d) => ({ ...d, song: saved })))
      .catch(toast.error)
  }, [snap.status, snap.rate, song.id, song.savedRate, setDetail, toast])

  // When the player lets go of a loop, by finishing it or because the user moved away, so does the page.
  useEffect(() => {
    if (!snap.loop) {
      setSpanLineId(null)
      setLooping(false)
    }
  }, [snap.loop])

  // Highlight the line being sung.
  useEffect(() => {
    if (!player) return
    return player.onTick((t) => {
      const s = live.current
      if (s.syncing || s.spanLineId || !s.snap.playing) return
      const active = activeLineAt(s.detail.lines, t)
      if (active && active.id !== s.cursorId) setCursorId(active.id)
    })
  }, [player])

  // Keep the highlighted line in view, and in sync mode the line that is waiting for its tap.
  const followId = syncing ? (lines[syncIndex]?.id ?? cursorId) : cursorId
  useEffect(() => {
    if (!followId) return
    const active = document.activeElement
    if (active instanceof HTMLTextAreaElement || active instanceof HTMLInputElement) return
    document.getElementById(`line-${followId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [followId])

  const actions = useMemo(() => {
    const fail = (err: unknown) => {
      toast.error(err)
      void reload()
    }
    const setLines = (next: Line[]) => setDetail((d) => ({ ...d, lines: next }))
    const patchLine = (line: Line) =>
      setDetail((d) => ({ ...d, lines: d.lines.map((l) => (l.id === line.id ? line : l)) }))

    const saveLine = (id: string, patch: LinePatch) => {
      // Show the edit at once; the server's answer then confirms it.
      setDetail((d) => ({ ...d, lines: d.lines.map((l) => (l.id === id ? { ...l, ...patch } : l)) }))
      api.updateLine(id, patch).then(patchLine).catch(fail)
    }

    const removeLine = async (line: Line) => {
      try {
        await api.deleteLine(line.id)
        await reload()
      } catch (err) {
        fail(err)
      }
    }

    const patchWord = (word: Word) =>
      setDetail((d) => ({ ...d, words: d.words.map((w) => (w.id === word.id ? word : w)) }))

    const addCards = (card: NewCard) => {
      api
        .addCards(card)
        .then((created) => {
          const refs: CardRef[] = created.map((c) => ({
            id: c.id,
            kind: c.kind,
            wordId: c.wordId,
            lineId: c.lineId,
            direction: c.direction,
          }))
          setDetail((d) => ({ ...d, cards: [...d.cards, ...refs] }))
          toast.info(
            created.length === 0
              ? 'Already in the deck.'
              : created.length === 2
                ? 'Added to the deck, in both directions.'
                : 'Added to the deck.',
          )
          refreshCounts()
        })
        .catch(fail)
    }

    const splitLine = (lineId: string) => {
      api.splitLine(lineId).then(reload).catch(fail)
    }

    const cursorLine = () => {
      const s = live.current
      return s.detail.lines.find((l) => l.id === s.cursorId) ?? null
    }

    /** Play one stretch of the song a number of times, holding the highlight on its line. */
    const playSpan = (
      lineId: string,
      start: number,
      end: number,
      repeats: number,
      pauseSec: number,
      leadInSec: number,
      onFinish?: () => void,
    ) => {
      const s = live.current
      if (!s.player || s.snap.status !== 'ready') return
      setCursorId(lineId)
      setSpanLineId(lineId)
      setLooping(repeats !== 1)
      s.player.startLoop({ start, end, repeats: repeats === 0 ? Infinity : repeats, pauseSec, leadInSec, onFinish })
    }

    const loopLine = (line: Line) => {
      const s = live.current
      if (line.startSec == null) return
      const { loopRepeats, loopPauseSec, loopLeadInSec } = s.settings
      playSpan(line.id, line.startSec, lineEnd(s.detail.lines, line)!, loopRepeats, loopPauseSec, loopLeadInSec)
    }

    const playLine = (line: Line, onFinish?: () => void) => {
      const s = live.current
      if (line.startSec == null) return
      playSpan(line.id, line.startSec, lineEnd(s.detail.lines, line)!, 1, 0, s.settings.loopLeadInSec, onFinish)
    }

    /** While a take is recorded, the line loops until the take stops. */
    const loopForTake = (line: Line) => {
      const s = live.current
      if (line.startSec == null) return
      playSpan(line.id, line.startSec, lineEnd(s.detail.lines, line)!, 0, s.settings.loopPauseSec, s.settings.loopLeadInSec)
    }

    const toggleLoop = () => {
      const s = live.current
      if (s.looping) {
        s.player?.stopLoop()
        return
      }
      const line = cursorLine()
      if (line) loopLine(line)
    }

    const startSync = (from?: number) => {
      const s = live.current
      const all = s.detail.lines
      if (all.length === 0) return
      s.player?.stopLoop()
      const firstUntimed = all.findIndex((l) => l.startSec == null)
      const index = from ?? (firstUntimed >= 0 ? firstUntimed : 0)
      const previous = all[index - 1]
      setSyncing(true)
      setSyncIndex(index)
      setCursorId(previous?.id ?? null)
      // Go back to the line before, so there is a run-up to the tap.
      if (previous?.startSec != null) s.player?.seek(previous.startSec)
      else if (index === 0) s.player?.seek(0)
    }

    const tap = () => {
      const s = live.current
      if (!s.player || !s.syncing) return
      const all = s.detail.lines
      // A tap always lands a little after the sound; take the reaction time back off.
      const lag = s.snap.playing ? s.settings.tapOffsetSec * s.snap.rate : 0
      const t = round2(Math.max(0, s.player.time() - lag))
      const index = s.syncIndex
      if (index >= all.length) {
        const last = all[all.length - 1]
        if (last?.startSec != null && t > last.startSec) saveLine(last.id, { endSec: t })
        setSyncing(false)
        toast.info('Every line is timed.')
        return
      }
      const line = all[index]
      const previous = all[index - 1]
      saveLine(line.id, { startSec: t, endSec: null })
      // A line ends where the next begins.
      if (previous?.startSec != null && t > previous.startSec) saveLine(previous.id, { endSec: t })
      setCursorId(line.id)
      setSyncIndex(index + 1)
    }

    const undoTap = () => {
      const s = live.current
      if (!s.syncing || s.syncIndex === 0) return
      const all = s.detail.lines
      const index = s.syncIndex - 1
      const previous = all[index - 1]
      saveLine(all[index].id, { startSec: null, endSec: null })
      setSyncIndex(index)
      setCursorId(previous?.id ?? null)
      s.player?.seek(Math.max(0, (previous?.startSec ?? 0) - 1))
    }

    const lineActions: LineActions = {
      saveLine,
      deleteLine(line) {
        const d = live.current.detail
        const hasWork =
          line.translit !== '' ||
          line.gloss !== '' ||
          line.startSec !== null ||
          d.words.some((w) => w.lineId === line.id) ||
          d.cards.some((c) => c.lineId === line.id) ||
          d.questions.some((q) => q.lineId === line.id) ||
          d.recordings.some((r) => r.lineId === line.id)
        if (hasWork) setDeleting(line)
        else void removeLine(line)
      },
      moveLine(id, delta) {
        api.moveLine(id, delta).then(setLines).catch(fail)
      },
      addLineAfter(line) {
        api
          .addBlankLine(line.songId, line.id)
          .then((next) => {
            // Put the cursor in the new line once it is on screen, so it can be typed straight away.
            focusLine.current = next[next.findIndex((l) => l.id === line.id) + 1]?.id ?? null
            setLines(next)
          })
          .catch(fail)
      },
      selectLine(line) {
        const s = live.current
        if (s.syncing) {
          startSync(s.detail.lines.findIndex((l) => l.id === line.id))
          return
        }
        setCursorId(line.id)
        if (line.startSec == null) return
        if (s.looping) loopLine(line)
        else {
          s.player?.stopLoop()
          s.player?.seek(line.startSec)
        }
      },
      playLine,
      nudgeLine(line, edge, direction) {
        const s = live.current
        const fresh = s.detail.lines.find((l) => l.id === line.id)
        if (!fresh || fresh.startSec == null) return
        const changes = nudge(s.detail.lines, fresh, edge, direction * NUDGE_SEC)
        if (changes.length === 0) return
        for (const change of changes) {
          const patch: LinePatch = {}
          if (change.startSec !== undefined) patch.startSec = change.startSec
          if (change.endSec !== undefined) patch.endSec = change.endSec
          saveLine(change.id, patch)
        }
        const mine = changes.find((c) => c.id === fresh.id)!
        const start = mine.startSec ?? fresh.startSec
        const end = mine.endSec ?? lineEnd(s.detail.lines, fresh)!
        if (s.looping && s.spanLineId === fresh.id) {
          // Keep looping, on the new edges.
          playSpan(fresh.id, start, end, s.settings.loopRepeats, s.settings.loopPauseSec, s.settings.loopLeadInSec)
        } else if (edge === 'start') {
          // Play from exactly the new start, with no lead-in, so the edge can be judged by ear.
          playSpan(fresh.id, start, Math.min(end, start + NUDGE_PREVIEW_SEC), 1, 0, 0)
        } else {
          playSpan(fresh.id, Math.max(start, end - NUDGE_PREVIEW_SEC), end, 1, 0, 0)
        }
      },
      syncFrom(line) {
        startSync(live.current.detail.lines.findIndex((l) => l.id === line.id))
      },
      toggleWords(lineId) {
        const d = live.current.detail
        const opening = !live.current.openWords.has(lineId) && !live.current.allWordsOpen
        setOpenWords((open) => {
          const next = new Set(open)
          if (next.has(lineId)) next.delete(lineId)
          else next.add(lineId)
          return next
        })
        // Opening a line for the first time splits it, so the words are there straight away.
        const line = d.lines.find((l) => l.id === lineId)
        if (opening && line?.arabic && !d.words.some((w) => w.lineId === lineId)) splitLine(lineId)
      },
      makeLineCard(line) {
        if (!line.gloss) {
          toast.info('Give the line an English gloss first, so the card has an answer.')
          return
        }
        addCards({ kind: 'line', lineId: line.id })
      },
      splitLine,
      addWord(lineId, arabic) {
        api
          .addWord(lineId, arabic)
          .then((word) => setDetail((d) => ({ ...d, words: [...d.words, word] })))
          .catch(fail)
      },
      saveWord(id, patch) {
        setDetail((d) => ({ ...d, words: d.words.map((w) => (w.id === id ? { ...w, ...patch } : w)) }))
        api.updateWord(id, patch).then(patchWord).catch(fail)
      },
      deleteWord(word) {
        api.deleteWord(word.id).then(reload).catch(fail)
      },
      setLineStatus(line, status) {
        saveLine(line.id, { status })
      },
      addQuestion(target, text) {
        api
          .addQuestion({ ...target, text })
          .then((q) => setDetail((d) => ({ ...d, questions: [...d.questions, q] })))
          .catch(fail)
      },
      resolveQuestion(question) {
        setDetail((d) => ({ ...d, questions: d.questions.map((q) => (q.id === question.id ? { ...q, resolved: true } : q)) }))
        api.updateQuestion(question.id, { resolved: true }).catch(fail)
      },
      deleteQuestion(question) {
        setDetail((d) => ({ ...d, questions: d.questions.filter((q) => q.id !== question.id) }))
        api.deleteQuestion(question.id).catch(fail)
      },
      makeWordCard(word) {
        if (!word.gloss) {
          toast.info('Give the word a meaning first, so the card has an answer.')
          return
        }
        addCards({ kind: 'word', wordId: word.id })
      },
    }

    const splitAllLines = async () => {
      const d = live.current.detail
      try {
        for (const line of d.lines) {
          if (line.arabic && !d.words.some((w) => w.lineId === line.id)) await api.splitLine(line.id)
        }
        setAllWordsOpen(true)
        await reload()
      } catch (err) {
        fail(err)
      }
    }

    const stepLine = (delta: -1 | 1) => {
      const s = live.current
      const all = s.detail.lines
      if (all.length === 0) return
      const index = all.findIndex((l) => l.id === s.cursorId)
      const target = all[Math.min(all.length - 1, Math.max(0, index + delta))]
      lineActions.selectLine(target)
    }

    const nudgeCursor = (edge: 'start' | 'end', direction: -1 | 1) => {
      const line = cursorLine()
      if (line) lineActions.nudgeLine(line, edge, direction)
    }

    return {
      ...lineActions,
      removeLine,
      setLines,
      fail,
      cursorLine,
      toggleLoop,
      startSync,
      tap,
      undoTap,
      stepLine,
      nudgeCursor,
      splitAllLines,
      loopForTake,
    }
  }, [reload, setDetail, toast, refreshCounts])

  const shadow = useRef<ShadowControls>(null)

  useHotkeys({
    r: () => !syncing && shadow.current?.toggleRecord(),
    c: () => !syncing && shadow.current?.compare(),
    ' ': () => player?.toggle(),
    ArrowUp: () => actions.stepLine(-1),
    ArrowDown: () => actions.stepLine(1),
    ArrowLeft: () => player?.seek(player.time() - SEEK_STEP_SEC),
    ArrowRight: () => player?.seek(player.time() + SEEK_STEP_SEC),
    Enter: () => {
      if (syncing) actions.tap()
      else {
        const line = actions.cursorLine()
        if (line) actions.playLine(line)
      }
    },
    Backspace: () => actions.undoTap(),
    l: () => !syncing && actions.toggleLoop(),
    s: () => (syncing ? setSyncing(false) : actions.startSync()),
    '-': () => player?.stepRate(-1),
    '=': () => player?.stepRate(1),
    '+': () => player?.stepRate(1),
    ',': () => actions.nudgeCursor('start', -1),
    '.': () => actions.nudgeCursor('start', 1),
    '<': () => actions.nudgeCursor('end', -1),
    '>': () => actions.nudgeCursor('end', 1),
    Escape: () => {
      if (syncing) setSyncing(false)
      else player?.stopLoop()
    },
  })

  const cursor = lines.find((l) => l.id === cursorId) ?? null

  // Words and cards grouped by line, so a row only re-renders when its own change.
  const wordsByLine = useMemo(() => {
    const map = new Map<string, Word[]>()
    for (const word of detail.words) {
      const list = map.get(word.lineId)
      if (list) list.push(word)
      else map.set(word.lineId, [word])
    }
    return map
  }, [detail.words])
  const questionsByLine = useMemo(() => {
    const map = new Map<string, Question[]>()
    for (const question of detail.questions) {
      if (!question.lineId || question.resolved) continue
      const list = map.get(question.lineId)
      if (list) list.push(question)
      else map.set(question.lineId, [question])
    }
    return map
  }, [detail.questions])
  const cardsByLine = useMemo(() => {
    const map = new Map<string, CardRef[]>()
    for (const card of detail.cards) {
      if (!card.lineId) continue
      const list = map.get(card.lineId)
      if (list) list.push(card)
      else map.set(card.lineId, [card])
    }
    return map
  }, [detail.cards])
  const timedCount = lines.filter((l) => l.startSec != null).length

  return (
    <div className="song-page">
      <header className="song-head">
        <a className="btn btn-ghost btn-sm btn-icon" href="#/" aria-label="Back to the library" title="Library">
          <ArrowLeft size={18} />
        </a>
        <div className="song-head-title">
          <h1 dir="auto">{song.title}</h1>
          {song.artist && <span dir="auto">{song.artist}</span>}
        </div>
        <div className="song-head-actions">
          <Button size="sm" icon={<Pencil size={14} />} onClick={() => setEditing(true)}>
            Details
          </Button>
        </div>
      </header>

      <div className="song-cols">
        <aside className="stage">
          <PlayerFrame hostRef={hostRef} snap={snap} youtubeId={song.youtubeId} onRetry={retry} />
          <Transport player={player} snap={snap} onRate={(rate) => player?.setRate(rate)} />
          {snap.status === 'ready' && snap.rates.length === 1 && (
            <p className="stage-note">YouTube does not offer other speeds for this video.</p>
          )}

          {!syncing && (
            <LoopPanel
              snap={snap}
              settings={settings}
              looping={looping}
              canLoop={snap.status === 'ready' && cursor?.startSec != null}
              onToggle={actions.toggleLoop}
              onSettings={(patch) => saveSettings(patch).catch(toast.error)}
            />
          )}
          {!syncing && (
            <ShadowPanel
              line={cursor}
              recording={detail.recordings.find((r) => r.lineId === cursorId) ?? null}
              canPlayLine={snap.status === 'ready' && cursor?.startSec != null}
              controls={shadow}
              onLoopForTake={actions.loopForTake}
              onStopLoop={() => {
                player?.stopLoop()
                player?.pause()
              }}
              onPlayLine={(line, onFinish) => actions.playLine(line, onFinish)}
              onSaved={(rec) =>
                setDetail((d) => ({ ...d, recordings: [...d.recordings.filter((r) => r.lineId !== rec.lineId), rec] }))
              }
              onDeleted={(lineId) => setDetail((d) => ({ ...d, recordings: d.recordings.filter((r) => r.lineId !== lineId) }))}
            />
          )}
          <SyncPanel
            lines={lines}
            syncing={syncing}
            syncIndex={syncIndex}
            playing={snap.playing}
            onStart={() => actions.startSync()}
            onTap={actions.tap}
            onUndo={actions.undoTap}
            onDone={() => setSyncing(false)}
          />

          <ShortcutList />

          {song.notes && (
            <section className="stage-section">
              <h2>Notes</h2>
              <p className="song-notes" dir="auto">
                {song.notes}
              </p>
            </section>
          )}
        </aside>

        <section className="lyrics" aria-label="Lyrics">
          <div className="lyrics-inner">
            {lines.length === 0 ? (
              <EmptyState
                title="Add the lyrics"
                action={
                  <Button variant="primary" icon={<ClipboardPaste size={16} />} onClick={() => setPasting(true)}>
                    Paste lyrics
                  </Button>
                }
              >
                <p>
                  Type or paste the Arabic, one line of the song per row. Each line then gets a transliteration and an
                  English gloss directly under it.
                </p>
              </EmptyState>
            ) : (
              <>
                <div className="lyrics-toolbar">
                  <span className="muted small">
                    {lines.length} {lines.length === 1 ? 'line' : 'lines'} · {timedCount} timed
                  </span>
                  <span className="push" />
                  {translating > 0 && (
                    <span className="translating" role="status">
                      <span className="spinner-dot" />
                      Claude is translating {translating} {translating === 1 ? 'line' : 'lines'}…
                    </span>
                  )}
                  {meta.claude && (
                    <Button size="sm" icon={<Sparkles size={14} />} onClick={() => setDrafting(true)}>
                      Draft with Claude
                    </Button>
                  )}
                  <Button
                    size="sm"
                    icon={<Rows3 size={14} />}
                    pressed={allWordsOpen}
                    onClick={() => {
                      setAllWordsOpen((open) => !open)
                      setOpenWords(new Set())
                    }}
                    title="Show or hide the word breakdown of every line"
                  >
                    Words
                  </Button>
                  <Button size="sm" icon={<ClipboardPaste size={14} />} onClick={() => setPasting(true)}>
                    Add lyrics
                  </Button>
                  <Menu
                    label="More lyric actions"
                    items={[
                      {
                        label: 'Split every line into words',
                        icon: <SplitSquareHorizontal size={15} />,
                        onSelect: () => void actions.splitAllLines(),
                      },
                      {
                        label: 'Clear all timings',
                        icon: <Eraser size={15} />,
                        danger: true,
                        disabled: timedCount === 0,
                        onSelect: () => setClearing(true),
                      },
                    ]}
                  />
                </div>
                <ol className="lines">
                  {lines.map((line, i) => (
                    <LineRow
                      key={line.id}
                      line={line}
                      number={i + 1}
                      isFirst={i === 0}
                      isLast={i === lines.length - 1}
                      isCurrent={line.id === cursorId}
                      isNextTap={syncing && i === syncIndex}
                      endSec={line.id === cursorId ? lineEnd(lines, line) : null}
                      words={wordsByLine.get(line.id) ?? NO_WORDS}
                      cards={cardsByLine.get(line.id) ?? NO_CARDS}
                      questions={questionsByLine.get(line.id) ?? NO_QUESTIONS}
                      wordsOpen={allWordsOpen || openWords.has(line.id)}
                      actions={actions}
                    />
                  ))}
                </ol>
              </>
            )}
          </div>
        </section>
      </div>

      {editing && (
        <EditSongDialog
          song={song}
          cardCount={detail.cards.length}
          onSaved={(saved) => setDetail((d) => ({ ...d, song: saved }))}
          onClose={() => setEditing(false)}
        />
      )}

      {pasting && (
        <PasteLyricsDialog
          hasLines={lines.length > 0}
          autoTranslate={meta.claude && settings.autoDraft}
          onClose={() => setPasting(false)}
          onAdd={async (text) => {
            try {
              const known = new Set(lines.map((l) => l.id))
              const next = await api.addLines(song.id, text)
              actions.setLines(next)
              autoTranslate(next.filter((l) => !known.has(l.id)).map((l) => l.id))
            } catch (err) {
              toast.error(err)
              throw err
            }
          }}
        />
      )}

      {deleting && (
        <Confirm
          title="Delete this line?"
          confirmLabel="Delete line"
          danger
          onClose={() => setDeleting(null)}
          onConfirm={() => {
            void actions.removeLine(deleting)
            setDeleting(null)
          }}
        >
          <p className="ar confirm-ar">{deleting.arabic}</p>
          <p>
            Its transliteration, gloss, timing, words, questions and recording go with it. Review cards made from it are
            kept, without their clip.
          </p>
        </Confirm>
      )}

      {drafting && (
        <DraftDialog
          songId={song.id}
          lines={lines}
          words={detail.words}
          model={meta.model}
          onDone={() => {
            void reload()
          }}
          onClose={() => setDrafting(false)}
        />
      )}

      {clearing && (
        <Confirm
          title="Clear all timings?"
          confirmLabel="Clear timings"
          danger
          onClose={() => setClearing(false)}
          onConfirm={() => {
            setClearing(false)
            player?.stopLoop()
            setSyncing(false)
            api.clearTimings(song.id).then(reload).catch(actions.fail)
          }}
        >
          <p>Every line loses its start and end time, and the song has to be tapped in again. The text is not touched.</p>
        </Confirm>
      )}
    </div>
  )
}

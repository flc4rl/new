import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ClipboardPaste, Pencil } from 'lucide-react'
import { api, type LinePatch } from '../api'
import { useHotkeys, useLoad, usePlayer } from '../lib/hooks'
import { Button, Confirm, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { PlayerFrame, Transport } from '../components/PlayerPanel'
import { EditSongDialog } from '../components/SongDialogs'
import { LineRow, type LineActions } from '../components/LineRow'
import { PasteLyricsDialog } from '../components/PasteLyricsDialog'
import type { Line, SongDetail } from '../../shared/types'

type SetDetail = (next: SongDetail | ((prev: SongDetail) => SongDetail)) => void

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
  const { hostRef, player, snap, retry } = usePlayer(song.youtubeId, song.savedRate)
  const [editing, setEditing] = useState(false)
  const [pasting, setPasting] = useState(false)
  const [deleting, setDeleting] = useState<Line | null>(null)

  // The newest state, for handlers that must not be recreated on every change.
  const live = useRef({ detail })
  live.current = { detail }

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

  const actions = useMemo(() => {
    const fail = (err: unknown) => {
      toast.error(err)
      void reload()
    }
    const setLines = (next: Line[]) => setDetail((d) => ({ ...d, lines: next }))
    const patchLine = (line: Line) =>
      setDetail((d) => ({ ...d, lines: d.lines.map((l) => (l.id === line.id ? line : l)) }))

    const removeLine = async (line: Line) => {
      try {
        await api.deleteLine(line.id)
        await reload()
      } catch (err) {
        fail(err)
      }
    }

    const lineActions: LineActions = {
      saveLine(id: string, patch: LinePatch) {
        // Show the edit at once; the server's answer then confirms it.
        setDetail((d) => ({ ...d, lines: d.lines.map((l) => (l.id === id ? { ...l, ...patch } : l)) }))
        api.updateLine(id, patch).then(patchLine).catch(fail)
      },
      deleteLine(line: Line) {
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
      moveLine(id: string, delta: -1 | 1) {
        api.moveLine(id, delta).then(setLines).catch(fail)
      },
      addLineAfter(line: Line) {
        api
          .addBlankLine(line.songId, line.id)
          .then((next) => {
            // Put the cursor in the new line once it is on screen, so it can be typed straight away.
            focusLine.current = next[next.findIndex((l) => l.id === line.id) + 1]?.id ?? null
            setLines(next)
          })
          .catch(fail)
      },
    }
    return { ...lineActions, removeLine, setLines, fail }
  }, [reload, setDetail, toast])

  useHotkeys({
    ' ': () => player?.toggle(),
    '-': () => player?.stepRate(-1),
    '=': () => player?.stepRate(1),
    '+': () => player?.stepRate(1),
  })

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
                    {lines.length} {lines.length === 1 ? 'line' : 'lines'}
                  </span>
                  <Button size="sm" className="push" icon={<ClipboardPaste size={14} />} onClick={() => setPasting(true)}>
                    Add lyrics
                  </Button>
                </div>
                <ol className="lines">
                  {lines.map((line, i) => (
                    <LineRow
                      key={line.id}
                      line={line}
                      number={i + 1}
                      isFirst={i === 0}
                      isLast={i === lines.length - 1}
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
          onClose={() => setPasting(false)}
          onAdd={async (text) => {
            try {
              actions.setLines(await api.addLines(song.id, text))
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
    </div>
  )
}

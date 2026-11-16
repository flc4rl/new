import { useEffect, useState } from 'react'
import { ArrowLeft, Pencil } from 'lucide-react'
import { api } from '../api'
import { useHotkeys, useLoad, usePlayer } from '../lib/hooks'
import { Button, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { PlayerFrame, Transport } from '../components/PlayerPanel'
import { EditSongDialog } from '../components/SongDialogs'
import type { SongDetail } from '../../shared/types'

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
  return <SongView detail={detail.data} setDetail={detail.setData} />
}

function SongView({
  detail,
  setDetail,
}: {
  detail: SongDetail
  setDetail: (next: SongDetail | ((prev: SongDetail) => SongDetail)) => void
}) {
  const { song } = detail
  const toast = useToast()
  const { hostRef, player, snap, retry } = usePlayer(song.youtubeId, song.savedRate)
  const [editing, setEditing] = useState(false)

  // Remember the last speed used for this song.
  useEffect(() => {
    if (snap.status !== 'ready' || snap.rate === song.savedRate) return
    api
      .updateSong(song.id, { savedRate: snap.rate })
      .then((saved) => setDetail((d) => ({ ...d, song: saved })))
      .catch(toast.error)
  }, [snap.status, snap.rate, song.id, song.savedRate, setDetail, toast])

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
          <EmptyState title="No lyrics yet">
            <p>Lyrics are added in the next build step.</p>
          </EmptyState>
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
    </div>
  )
}

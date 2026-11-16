import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Plus } from 'lucide-react'
import { api, type YouTubeLookup } from '../api'
import { go, useLoad } from '../lib/hooks'
import { Button, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { parseYouTubeId } from '../../shared/youtube'
import type { SongSummary } from '../../shared/types'

function Meter({ label, value, total }: { label: string; value: number; total: number }) {
  const ratio = total > 0 ? Math.min(1, value / total) : 0
  return (
    <div className="meter">
      <div className="meter-row">
        <span>{label}</span>
        <span className="meter-num">
          {value}
          <span className="meter-of">/{total}</span>
        </span>
      </div>
      <div className="meter-bar" aria-hidden="true">
        <span style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  )
}

function SongCard({ song }: { song: SongSummary }) {
  const [thumbOk, setThumbOk] = useState(true)
  return (
    <a className="song-card" href={`#/song/${song.id}`}>
      <div className="song-thumb">
        {thumbOk && (
          <img
            src={`https://i.ytimg.com/vi/${song.youtubeId}/mqdefault.jpg`}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setThumbOk(false)}
          />
        )}
      </div>
      <div className="song-card-body">
        <h3 dir="auto">{song.title}</h3>
        <p className="song-artist" dir="auto">
          {song.artist || ' '}
        </p>
        {song.lineCount === 0 ? (
          <p className="song-none">No lyrics yet</p>
        ) : (
          <div className="meters">
            <Meter label="Synced" value={song.syncedCount} total={song.lineCount} />
            <Meter label="Verified" value={song.verifiedCount} total={song.lineCount} />
            <div className="meter">
              <div className="meter-row">
                <span>Cards</span>
                <span className="meter-num">{song.cardCount}</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </a>
  )
}

function AddSongForm({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const toast = useToast()
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [notes, setNotes] = useState('')
  const [found, setFound] = useState<YouTubeLookup | null>(null)
  const [saving, setSaving] = useState(false)
  const titleTouched = useRef(false)

  const youtubeId = parseYouTubeId(url)
  const badLink = url.trim().length > 0 && !youtubeId

  // Ask YouTube for the public title once the link is recognisable.
  useEffect(() => {
    setFound(null)
    if (!youtubeId) return
    let cancelled = false
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.lookup(url)
        if (cancelled) return
        setFound(result)
        if (result.title && !titleTouched.current) setTitle(result.title)
      } catch {
        // The lookup is a convenience; the form works without it.
      }
    }, 300)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [youtubeId])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!youtubeId || !title.trim()) return
    setSaving(true)
    try {
      const song = await api.addSong({ url, title, artist, notes })
      onDone()
      go(`/song/${song.id}`)
    } catch (err) {
      toast.error(err)
      setSaving(false)
    }
  }

  return (
    <form className="panel add-song" onSubmit={submit}>
      <h2>Add a song</h2>
      <label className="field">
        <span>YouTube link</span>
        <input
          type="url"
          inputMode="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=…"
          autoFocus
          required
          aria-invalid={badLink}
        />
        {badLink && <small className="field-error">That does not look like a YouTube link.</small>}
        {found?.available === true && found.title && (
          <small className="field-hint" dir="auto">
            Found: {found.title}
            {found.author ? ` · ${found.author}` : ''}
          </small>
        )}
        {found?.available === false && (
          <small className="field-warn">
            YouTube would not share details for this video. It may be private or may not allow playing inside other
            apps. You can add it and try.
          </small>
        )}
      </label>
      <div className="field-row">
        <label className="field">
          <span>Title</span>
          <input
            value={title}
            dir="auto"
            onChange={(e) => {
              titleTouched.current = true
              setTitle(e.target.value)
            }}
            required
          />
        </label>
        <label className="field">
          <span>Artist</span>
          <input value={artist} dir="auto" onChange={(e) => setArtist(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Notes</span>
        <textarea
          value={notes}
          dir="auto"
          rows={2}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Where you heard it, what the tutor said about it…"
        />
      </label>
      <div className="form-actions">
        {onCancel && <Button onClick={onCancel}>Cancel</Button>}
        <Button type="submit" variant="primary" disabled={!youtubeId || !title.trim() || saving}>
          {saving ? 'Adding…' : 'Add song'}
        </Button>
      </div>
    </form>
  )
}

export function LibraryPage() {
  const songs = useLoad(api.songs, [])
  const [adding, setAdding] = useState(false)

  if (songs.error) return <LoadError error={songs.error} onRetry={songs.reload} />
  if (!songs.data) return <Spinner />

  const empty = songs.data.length === 0

  return (
    <div className="page">
      <div className="page-head">
        <h1>Library</h1>
        {!empty && !adding && (
          <Button variant="primary" icon={<Plus size={16} />} onClick={() => setAdding(true)}>
            Add song
          </Button>
        )}
      </div>

      {empty && (
        <EmptyState title="Start with one song you like">
          <p>
            Paste a YouTube link below. Then paste the lyrics, tap along once to time each line, and study the song
            slowed down, line by line.
          </p>
        </EmptyState>
      )}

      {(adding || empty) && (
        <AddSongForm onDone={() => setAdding(false)} onCancel={empty ? undefined : () => setAdding(false)} />
      )}

      {!empty && (
        <div className="song-grid">
          {songs.data.map((song) => (
            <SongCard key={song.id} song={song} />
          ))}
        </div>
      )}
    </div>
  )
}

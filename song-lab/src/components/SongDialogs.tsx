import { useState, type FormEvent } from 'react'
import { Trash2 } from 'lucide-react'
import { api } from '../api'
import { go } from '../lib/hooks'
import { Button, Confirm, Modal, useToast } from './ui'
import { parseYouTubeId, watchUrl } from '../../shared/youtube'
import type { Song } from '../../shared/types'

export function EditSongDialog({
  song,
  cardCount,
  onSaved,
  onClose,
}: {
  song: Song
  cardCount: number
  onSaved: (song: Song) => void
  onClose: () => void
}) {
  const toast = useToast()
  const [title, setTitle] = useState(song.title)
  const [artist, setArtist] = useState(song.artist)
  const [notes, setNotes] = useState(song.notes)
  const [url, setUrl] = useState(watchUrl(song.youtubeId))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const badLink = !parseYouTubeId(url)

  async function save(e: FormEvent) {
    e.preventDefault()
    try {
      onSaved(await api.updateSong(song.id, { title, artist, notes, url }))
      onClose()
    } catch (err) {
      toast.error(err)
    }
  }

  async function remove(keepCards: boolean) {
    try {
      await api.deleteSong(song.id, keepCards)
      go('/')
    } catch (err) {
      toast.error(err)
    }
  }

  if (confirmDelete) {
    return (
      <Confirm
        title={`Delete “${song.title}”?`}
        confirmLabel={cardCount > 0 ? 'Delete song and cards' : 'Delete song'}
        danger
        onConfirm={() => remove(false)}
        onClose={() => setConfirmDelete(false)}
        extra={cardCount > 0 ? <Button onClick={() => remove(true)}>Delete song, keep cards</Button> : undefined}
      >
        <p>This deletes the song with its lines, words, recordings and questions. It cannot be undone.</p>
        {cardCount > 0 && (
          <p>
            The song has {cardCount} review {cardCount === 1 ? 'card' : 'cards'}. Kept cards hold on to their text and
            review history, but can no longer play a clip.
          </p>
        )}
      </Confirm>
    )
  }

  return (
    <Modal title="Song details" onClose={onClose}>
      <form className="form-grid" onSubmit={save}>
        <label className="field">
          <span>Title</span>
          <input value={title} dir="auto" onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <label className="field">
          <span>Artist</span>
          <input value={artist} dir="auto" onChange={(e) => setArtist(e.target.value)} />
        </label>
        <label className="field">
          <span>YouTube link</span>
          <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} required aria-invalid={badLink} />
          {badLink && <small className="field-error">That does not look like a YouTube link.</small>}
        </label>
        <label className="field">
          <span>Notes</span>
          <textarea value={notes} dir="auto" rows={3} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <footer className="modal-foot">
          <Button variant="danger" className="push" icon={<Trash2 size={15} />} onClick={() => setConfirmDelete(true)}>
            Delete song
          </Button>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={badLink || !title.trim()}>
            Save
          </Button>
        </footer>
      </form>
    </Modal>
  )
}

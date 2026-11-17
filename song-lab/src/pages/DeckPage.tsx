import { useMemo, useState } from 'react'
import { ArrowLeft, Pause, Play, Plus, Trash2 } from 'lucide-react'
import { api } from '../api'
import { useApp } from '../App'
import { useLoad } from '../lib/hooks'
import { Button, Confirm, EmptyState, LoadError, Spinner, useToast } from '../components/ui'
import { AddPhraseDialog } from '../components/AddPhraseDialog'
import { CommitInput } from '../components/CommitInput'
import type { CardView, Settings } from '../../shared/types'

type Filter = 'all' | 'new' | 'learning' | 'suspended' | 'phrase'

function dueLabel(card: CardView, now: Date): string {
  if (card.suspended) return 'suspended'
  if (card.isNew) return 'new'
  const due = new Date(card.due)
  const days = Math.round((due.getTime() - now.getTime()) / 86_400_000)
  if (due <= now) return 'due now'
  if (days <= 0) return 'later today'
  if (days === 1) return 'tomorrow'
  return `in ${days} days`
}

function matches(card: CardView, filter: Filter, search: string): boolean {
  if (filter === 'new' && !card.isNew) return false
  if (filter === 'learning' && (card.isNew || card.suspended)) return false
  if (filter === 'suspended' && !card.suspended) return false
  if (filter === 'phrase' && card.kind !== 'phrase') return false
  if (!search) return true
  const q = search.toLowerCase()
  return [card.arabic, card.translit, card.gloss, card.songTitle ?? ''].some((t) => t.toLowerCase().includes(q))
}

export function DeckPage() {
  const toast = useToast()
  const { settings, saveSettings, refreshCounts } = useApp()
  const cards = useLoad(api.cards, [])
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<CardView | null>(null)
  const now = useMemo(() => new Date(), [cards.data])

  if (cards.error) return <LoadError error={cards.error} onRetry={cards.reload} />
  if (!cards.data) return <Spinner />

  const list = cards.data.filter((c) => matches(c, filter, search.trim()))
  const save = (patch: Partial<Settings>) => saveSettings(patch).then(refreshCounts).catch(toast.error)

  async function update(card: CardView, patch: { suspended?: boolean; arabic?: string; translit?: string; gloss?: string }) {
    try {
      const next = await api.updateCard(card.id, patch)
      cards.setData((all) => all.map((c) => (c.id === card.id ? next : c)))
      refreshCounts()
    } catch (err) {
      toast.error(err)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div className="page-title-row">
          <a className="btn btn-ghost btn-sm btn-icon" href="#/review" aria-label="Back to review" title="Review">
            <ArrowLeft size={18} />
          </a>
          <h1>All cards</h1>
        </div>
        <div className="page-head-actions">
          <Button variant="primary" icon={<Plus size={16} />} onClick={() => setAdding(true)}>
            Add a phrase
          </Button>
        </div>
      </div>

      <section className="panel deck-settings" aria-label="Review settings">
        <label className="inline-field">
          <span>New cards a day</span>
          <input
            type="number"
            min={0}
            max={500}
            value={settings.newPerDay}
            onChange={(e) => {
              const n = Math.round(Number(e.target.value))
              if (Number.isFinite(n) && n >= 0 && n <= 500) void save({ newPerDay: n })
            }}
            style={{ width: '5em' }}
          />
        </label>
        <label className="inline-field">
          <span>Making a card from a song adds</span>
          <select value={settings.cardDirections} onChange={(e) => void save({ cardDirections: e.target.value as Settings['cardDirections'] })}>
            <option value="both">both directions</option>
            <option value="ar-en">Arabic to English only</option>
            <option value="en-ar">English to Arabic only</option>
          </select>
        </label>
      </section>

      {cards.data.length === 0 ? (
        <EmptyState title="No cards yet">
          <p>Open a song and click the card button next to a line or a word, or add a phrase by hand.</p>
        </EmptyState>
      ) : (
        <>
          <div className="deck-filters">
            <div className="segmented" role="group" aria-label="Show">
              {(['all', 'new', 'learning', 'suspended', 'phrase'] as Filter[]).map((f) => (
                <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {f === 'phrase' ? 'phrases' : f}
                </button>
              ))}
            </div>
            <input
              className="input deck-search"
              type="search"
              placeholder="Search Arabic, transliteration or English"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              dir="auto"
            />
            <span className="muted small">
              {list.length} of {cards.data.length}
            </span>
          </div>

          <ul className="deck-list">
            {list.map((card) => (
              <li key={card.id} className={`deck-row${card.suspended ? ' is-suspended' : ''}`}>
                <div className="deck-text">
                  {card.kind === 'phrase' ? (
                    <>
                      <CommitInput rtl className="deck-ar" value={card.arabic} label="Arabic" onCommit={(arabic) => arabic && update(card, { arabic })} />
                      <CommitInput className="deck-tr" value={card.translit} label="Transliteration" onCommit={(translit) => update(card, { translit })} />
                      <CommitInput className="deck-gl" value={card.gloss} label="English" onCommit={(gloss) => gloss && update(card, { gloss })} />
                    </>
                  ) : (
                    <>
                      <p className="ar deck-ar" lang="ar">
                        {card.arabic}
                      </p>
                      <p className="ltr deck-tr">{card.translit}</p>
                      <p className="ltr deck-gl">{card.gloss}</p>
                    </>
                  )}
                </div>
                <div className="deck-meta">
                  <span>{card.direction === 'ar-en' ? 'Arabic → English' : 'English → Arabic'}</span>
                  <span>
                    {card.kind}
                    {card.songTitle ? (
                      <>
                        {' · '}
                        <a href={`#/song/${card.songId}`} dir="auto">
                          {card.songTitle}
                        </a>
                      </>
                    ) : null}
                  </span>
                  <span className={`deck-due${card.suspended ? '' : card.isNew ? ' is-new' : ''}`}>{dueLabel(card, now)}</span>
                  {card.status === 'draft' && <span className="tag tag-draft">draft</span>}
                </div>
                <div className="deck-actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={card.suspended ? <Play size={15} /> : <Pause size={15} />}
                    aria-label={card.suspended ? 'Bring the card back' : 'Suspend the card'}
                    title={card.suspended ? 'Bring back into review' : 'Suspend: keep the card but leave it out of review'}
                    onClick={() => void update(card, { suspended: !card.suspended })}
                  />
                  <Button size="sm" variant="ghost" icon={<Trash2 size={15} />} aria-label="Delete the card" title="Delete" onClick={() => setDeleting(card)} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {adding && (
        <AddPhraseDialog
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false)
            void cards.reload()
          }}
        />
      )}
      {deleting && (
        <Confirm
          title="Delete this card?"
          confirmLabel="Delete card"
          danger
          onClose={() => setDeleting(null)}
          onConfirm={() => {
            const target = deleting
            setDeleting(null)
            api
              .deleteCard(target.id)
              .then(() => {
                cards.setData((all) => all.filter((c) => c.id !== target.id))
                refreshCounts()
              })
              .catch(toast.error)
          }}
        >
          <p className="ar confirm-ar">{deleting.arabic}</p>
          <p>The card and its review history are deleted. The song, line and word are not touched.</p>
        </Confirm>
      )}
    </div>
  )
}

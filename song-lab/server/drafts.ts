import { randomUUID } from 'node:crypto'
import { claudeAvailable } from './config'
import { claudeDrafter, type DraftLine, type DraftWord, type Drafter } from './claude'
import { run, tx } from './db'
import { HttpError } from './http'
import * as store from './store'
import { splitWords } from '../shared/text'
import type { DraftResult, Line, Word } from '../shared/types'

/** Lines per request to Claude: small enough that one answer never runs out of room. */
const BATCH_SIZE = 12

let drafter: Drafter = claudeDrafter

/** Replace the drafter, for tests. */
export function setDrafter(next: Drafter | null): void {
  drafter = next ?? claudeDrafter
}

export function draftingAvailable(): boolean {
  return drafter !== claudeDrafter || claudeAvailable()
}

/** Compare Arabic without vowel marks, tatweel and punctuation. */
export function normalizeArabic(text: string): string {
  return text
    .normalize('NFC')
    .replace(/[ً-ٰٟۖ-ۭـ]/g, '')
    .replace(/[\p{P}\p{S}]/gu, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** True when a word Claude returned is really in the line, so no new lyrics can slip in. */
function wordIsInLine(word: string, lineArabic: string): boolean {
  const w = normalizeArabic(word)
  if (!w || w.includes(' ')) return false
  return normalizeArabic(lineArabic).split(' ').includes(w)
}

const filled = (current: string, next: string) => current.trim() === '' && next.trim() !== ''

interface Outcome {
  lineChanged: boolean
  wordsChanged: number
  skipped: string[]
}

/** Fill a draft word's empty fields. A verified word is never touched. */
function fillWord(word: Word, draft: DraftWord): boolean {
  if (word.status === 'verified') return false
  const patch: store.WordPatch = {}
  if (filled(word.translit, draft.translit)) patch.translit = draft.translit.trim()
  if (filled(word.gloss, draft.gloss)) patch.gloss = draft.gloss.trim()
  if (filled(word.root, draft.root)) patch.root = draft.root.trim()
  if (filled(word.note, draft.note)) patch.note = draft.note.trim()
  if (word.register === 'unknown' && draft.register !== 'unknown') patch.register = draft.register
  if (Object.keys(patch).length === 0) return false
  if (draft.unsure) patch.unsure = true
  store.updateWord(word.id, patch)
  return true
}

/** Save one line's draft. Only empty fields are filled, and everything stays a draft. */
export function applyDraft(line: Line, draft: DraftLine, number: number): Outcome {
  const outcome: Outcome = { lineChanged: false, wordsChanged: 0, skipped: [] }

  if (line.status !== 'verified') {
    const patch: store.LinePatch = {}
    if (filled(line.translit, draft.translit)) patch.translit = draft.translit.trim()
    if (filled(line.gloss, draft.gloss)) patch.gloss = draft.gloss.trim()
    if (filled(line.note, draft.note)) patch.note = draft.note.trim()
    if (Object.keys(patch).length > 0) {
      patch.source = 'claude'
      if (draft.unsure) patch.unsure = true
      store.updateLine(line.id, patch)
      outcome.lineChanged = true
    }
  }

  const strays = draft.words.filter((w) => !wordIsInLine(w.arabic, line.arabic))
  if (strays.length > 0) {
    outcome.skipped.push(`Line ${number}: ${strays.length === 1 ? 'a word' : `${strays.length} words`} that ${strays.length === 1 ? 'is' : 'are'} not in the line ${strays.length === 1 ? 'was' : 'were'} left out.`)
  }
  const suggestions = draft.words.filter((w) => !strays.includes(w))

  let words = store.wordsOfLine(line.id)
  if (words.length === 0) {
    // No split yet: take Claude's, as long as every word is really in the line; otherwise split on spaces.
    const split = strays.length === 0 && suggestions.length > 0 ? suggestions.map((w) => w.arabic.trim()) : splitWords(line.arabic)
    split.forEach((arabic, order) => {
      run('INSERT INTO words (id, lineId, ord, arabic) VALUES (?, ?, ?, ?)', randomUUID(), line.id, order, arabic)
    })
    words = store.wordsOfLine(line.id)
    if (words.length > 0) outcome.lineChanged = true
  }

  // Pair Claude's words with the line's words by their letters, in order.
  const used = new Set<number>()
  for (const word of words) {
    const key = normalizeArabic(word.arabic)
    const index = suggestions.findIndex((s, i) => !used.has(i) && normalizeArabic(s.arabic) === key)
    if (index < 0) continue
    used.add(index)
    if (fillWord(word, suggestions[index])) outcome.wordsChanged++
  }
  return outcome
}

/** Draft transliteration, gloss and words for some lines of a song. */
export async function draftLines(songId: string, lineIds: string[]): Promise<DraftResult> {
  if (!draftingAvailable()) {
    throw new HttpError(503, 'Drafting needs an Anthropic API key. Set ANTHROPIC_API_KEY and restart the app.')
  }
  const song = store.getSong(songId)
  const lines = store.linesOfSong(songId)
  const wanted = new Set(lineIds)
  const selected = lines
    .map((line, index) => ({ line, number: index + 1 }))
    .filter(({ line }) => wanted.has(line.id))
  if (selected.length === 0) throw new HttpError(400, 'Choose at least one line to draft.')

  const result: DraftResult = { updatedLines: 0, updatedWords: 0, skipped: [] }
  const todo = selected.filter(({ line, number }) => {
    if (line.arabic.trim()) return true
    result.skipped.push(`Line ${number} has no Arabic yet.`)
    return false
  })

  for (let start = 0; start < todo.length; start += BATCH_SIZE) {
    const batch = todo.slice(start, start + BATCH_SIZE)
    const request = batch.map(({ line }, i) => ({
      key: `L${i + 1}`,
      arabic: line.arabic,
      words: store.wordsOfLine(line.id).map((w) => w.arabic),
    }))
    const drafts = await drafter(request, { title: song.title, artist: song.artist })
    const byKey = new Map(drafts.map((d) => [d.key, d]))
    tx(() => {
      batch.forEach(({ line, number }, i) => {
        const draft = byKey.get(`L${i + 1}`)
        if (!draft) {
          result.skipped.push(`Line ${number}: Claude gave no answer for it.`)
          return
        }
        // Read the line again: the user may have edited it while Claude was working.
        const fresh = store.getLine(line.id)
        if (fresh.arabic !== line.arabic) {
          result.skipped.push(`Line ${number} changed while Claude was working, so its draft was not saved.`)
          return
        }
        const outcome = applyDraft(fresh, draft, number)
        if (outcome.lineChanged || outcome.wordsChanged > 0) result.updatedLines++
        result.updatedWords += outcome.wordsChanged
        result.skipped.push(...outcome.skipped)
      })
    })
  }
  return result
}

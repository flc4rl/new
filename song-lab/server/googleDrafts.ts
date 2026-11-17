import { randomUUID } from 'node:crypto'
import { run, tx } from './db'
import { googleLookup, shortMeaning } from './google'
import { HttpError } from './http'
import * as store from './store'
import { splitWords } from '../shared/text'
import type { DraftResult, GoogleLookup, Line } from '../shared/types'

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * The first of the candidates that appears as whole words in the English, as it is written there.
 * Common endings are allowed, so "disappear" finds "disappeared" and "rose" finds "roses".
 */
export function findInGloss(gloss: string, candidates: string[]): string {
  for (const candidate of candidates) {
    const needle = candidate.trim()
    if (needle.length < 2) continue
    const pattern = new RegExp(`(?<![\\p{L}\\p{N}'])${escape(needle)}(?:s|es|d|ed|ing)?(?![\\p{L}\\p{N}'])`, 'iu')
    const match = pattern.exec(gloss)
    if (match) return match[0]
  }
  return ''
}

function candidatesOf(result: GoogleLookup, ownMeaning: string): string[] {
  const all = [ownMeaning, ...result.dictionary.flatMap((d) => d.terms), result.translation]
  // "the sun" and "sun" are both worth trying.
  return all.flatMap((t) => [t, t.replace(/^(the|a|an|to|be)\s+/i, '')]).filter(Boolean)
}

/** Fill one line from Google: the English of the line, its words, and their meanings. */
async function translateLine(line: Line, result: DraftResult): Promise<void> {
  let changed = false
  if (line.status !== 'verified' && !line.gloss.trim()) {
    const english = (await googleLookup(line.arabic)).translation
    if (english) {
      store.updateLine(line.id, { gloss: english, source: 'google' })
      changed = true
    }
  }
  if (store.wordsOfLine(line.id).length === 0) {
    tx(() => {
      splitWords(line.arabic).forEach((arabic, order) => {
        run('INSERT INTO words (id, lineId, ord, arabic) VALUES (?, ?, ?, ?)', randomUUID(), line.id, order, arabic)
      })
    })
    changed = true
  }
  const gloss = store.getLine(line.id).gloss
  for (const word of store.wordsOfLine(line.id)) {
    if (word.status === 'verified' || (word.gloss && word.glossPart)) continue
    const lookup = await googleLookup(word.arabic)
    const patch: store.WordPatch = {}
    if (!word.gloss) {
      const meaning = shortMeaning(lookup)
      if (meaning) patch.gloss = meaning
    }
    if (!word.glossPart) {
      const part = findInGloss(gloss, candidatesOf(lookup, word.gloss))
      if (part) patch.glossPart = part
    }
    if (Object.keys(patch).length > 0) {
      store.updateWord(word.id, patch)
      result.updatedWords++
      changed = true
    }
  }
  if (changed) result.updatedLines++
}

/** Translate some lines of a song with Google. Stops cleanly, keeping what was done, if Google refuses. */
export async function googleTranslateLines(songId: string, lineIds: string[]): Promise<DraftResult> {
  if (!store.getSettings().googleTranslate) throw new HttpError(503, 'Google Translate is switched off in Settings.')
  const wanted = new Set(lineIds)
  const lines = store.linesOfSong(songId).map((line, i) => ({ line, number: i + 1 })).filter(({ line }) => wanted.has(line.id))
  if (lines.length === 0) throw new HttpError(400, 'Choose at least one line to translate.')

  const result: DraftResult = { updatedLines: 0, updatedWords: 0, skipped: [] }
  for (const { line, number } of lines) {
    if (!line.arabic.trim()) {
      result.skipped.push(`Line ${number} has no Arabic yet.`)
      continue
    }
    try {
      await translateLine(store.getLine(line.id), result)
    } catch (err) {
      if (!(err instanceof HttpError)) throw err
      if (result.updatedLines === 0 && result.updatedWords === 0) throw err
      result.skipped.push(`Stopped at line ${number}: ${err.message}`)
      break
    }
  }
  return result
}

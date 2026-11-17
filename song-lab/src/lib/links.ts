import type { Word } from '../../shared/types'

/** A run of text, and the word it belongs to (null for spaces, punctuation and unlinked words). */
export interface Segment {
  text: string
  wordId: string | null
}

const ARABIC_MARKS = /[ً-ٰٟۖ-ۭـ]/g
const EDGE_PUNCT = /^[\p{P}\p{S}]+|[\p{P}\p{S}]+$/gu

function normArabic(token: string): string {
  return token.normalize('NFC').replace(ARABIC_MARKS, '').replace(/[\p{P}\p{S}]/gu, '')
}

/** Keep the apostrophe (glottal stop) and hyphens: they are part of a transliterated word. */
function normLatin(token: string): string {
  return token.toLowerCase().replace(/^[^\p{L}\p{N}']+|[^\p{L}\p{N}']+$/gu, '')
}

/** Split text into words and the spaces between them, keeping every character. */
function tokens(text: string): string[] {
  return text.split(/(\s+)/).filter((t) => t !== '')
}

/** Split off leading and trailing punctuation so only the word itself is highlighted. */
function wordPieces(token: string, wordId: string): Segment[] {
  const lead = token.match(/^[\p{P}\p{S}]+/u)?.[0] ?? ''
  const trail = token.slice(lead.length).match(/[\p{P}\p{S}]+$/u)?.[0] ?? ''
  const core = token.slice(lead.length, token.length - trail.length)
  if (!core) return [{ text: token, wordId: null }]
  return [
    ...(lead ? [{ text: lead, wordId: null }] : []),
    { text: core, wordId },
    ...(trail ? [{ text: trail, wordId: null }] : []),
  ]
}

/**
 * Pair the tokens of a line with words, in order. A token pairs with the next unused word whose
 * key matches, looking a few words ahead so one odd token does not throw off the rest.
 */
function pairInOrder(text: string, words: Word[], keyOfToken: (t: string) => string, keyOfWord: (w: Word) => string): Segment[] {
  const out: Segment[] = []
  let next = 0
  const parts = tokens(text)
  const wordTokens = parts.filter((t) => !/^\s+$/.test(t))
  let matched = 0
  for (const part of parts) {
    if (/^\s+$/.test(part)) {
      out.push({ text: part, wordId: null })
      continue
    }
    const key = keyOfToken(part)
    let hit = -1
    for (let i = next; key && i < Math.min(words.length, next + 3); i++) {
      if (keyOfWord(words[i]) === key) {
        hit = i
        break
      }
    }
    if (hit >= 0) {
      out.push(...wordPieces(part, words[hit].id))
      next = hit + 1
      matched++
    } else {
      out.push({ text: part, wordId: null })
    }
  }
  // Nothing matched but the counts agree (say, the transliteration was typed differently): pair by position.
  if (matched === 0 && wordTokens.length === words.length && words.length > 0) {
    let i = 0
    return parts.flatMap((part) => (/^\s+$/.test(part) ? [{ text: part, wordId: null }] : wordPieces(part, words[i++].id)))
  }
  return out
}

export function linkArabic(text: string, words: Word[]): Segment[] {
  return pairInOrder(text, words, normArabic, (w) => normArabic(w.arabic))
}

export function linkTranslit(text: string, words: Word[]): Segment[] {
  const usable = words.filter((w) => w.translit.trim() && !/\s/.test(w.translit.trim()))
  return pairInOrder(text, usable, normLatin, (w) => normLatin(w.translit))
}

const isWordChar = (ch: string | undefined) => !!ch && /[\p{L}\p{N}']/u.test(ch)

/** Where in the English each word's meaning appears: its glossPart, or failing that its own meaning if it is there. */
export function linkGloss(text: string, words: Word[]): Segment[] {
  const lower = text.toLowerCase()
  const taken: { start: number; end: number; wordId: string }[] = []
  const overlaps = (start: number, end: number) => taken.some((r) => start < r.end && end > r.start)

  for (const word of words) {
    const candidates = [word.glossPart.trim(), word.gloss.trim().length >= 2 ? word.gloss.trim() : ''].filter(Boolean)
    for (const candidate of candidates) {
      const needle = candidate.replace(EDGE_PUNCT, '').toLowerCase()
      if (!needle) continue
      let from = 0
      let found = -1
      while ((from = lower.indexOf(needle, from)) >= 0) {
        const end = from + needle.length
        if (!isWordChar(text[from - 1]) && !isWordChar(text[end]) && !overlaps(from, end)) {
          found = from
          break
        }
        from += 1
      }
      if (found >= 0) {
        taken.push({ start: found, end: found + needle.length, wordId: word.id })
        break
      }
    }
  }

  taken.sort((a, b) => a.start - b.start)
  const out: Segment[] = []
  let at = 0
  for (const r of taken) {
    if (r.start > at) out.push({ text: text.slice(at, r.start), wordId: null })
    out.push({ text: text.slice(r.start, r.end), wordId: r.wordId })
    at = r.end
  }
  if (at < text.length) out.push({ text: text.slice(at), wordId: null })
  return out
}

export const hasLinks = (segments: Segment[]) => segments.some((s) => s.wordId)

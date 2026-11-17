// Google Translate, through the free address the translate.google.com website itself uses.
// It is unofficial: Google may change it or refuse requests at any time, so every answer is a
// draft, every lookup is cached, requests go one at a time with a pause between them, and a
// refusal stops further requests for a while instead of trying again and again.

import { get, run } from './db'
import { HttpError } from './http'
import type { GoogleEntry, GoogleLookup } from '../shared/types'

/** Changeable for tests. */
const baseUrl = () => process.env.SONGLAB_GOOGLE_URL || 'https://translate.googleapis.com'

const GAP_MS = 350
const COOLDOWN_MS = 10 * 60_000
const TIMEOUT_MS = 10_000

let queue: Promise<unknown> = Promise.resolve()
let lastRequest = 0
let refusedUntil = 0

export function resetGoogleForTests(): void {
  lastRequest = 0
  refusedUntil = 0
}

interface RawSentence {
  trans?: string
  orig?: string
  src_translit?: string
}

interface RawDict {
  pos?: string
  terms?: string[]
  entry?: { word?: string }[]
}

interface RawAnswer {
  sentences?: RawSentence[]
  dict?: RawDict[]
}

export function parseAnswer(raw: RawAnswer): GoogleLookup {
  const sentences = Array.isArray(raw.sentences) ? raw.sentences : []
  const translation = sentences
    .map((s) => (typeof s.trans === 'string' ? s.trans : ''))
    .join('')
    .trim()
  const romanization = sentences.find((s) => typeof s.src_translit === 'string')?.src_translit?.trim() ?? ''
  const dictionary: GoogleEntry[] = (Array.isArray(raw.dict) ? raw.dict : [])
    .map((d) => ({
      pos: typeof d.pos === 'string' ? d.pos : '',
      terms: (Array.isArray(d.terms) ? d.terms : (d.entry ?? []).map((e) => e.word ?? ''))
        .filter((t): t is string => typeof t === 'string' && t.trim() !== '')
        .slice(0, 8),
    }))
    .filter((d) => d.terms.length > 0)
  return { translation, romanization, dictionary }
}

const REFUSED =
  'Google Translate is not answering this computer just now (too many requests, or Google has changed something). Try again in a while; nothing else is affected.'

async function request(text: string): Promise<GoogleLookup> {
  if (Date.now() < refusedUntil) throw new HttpError(503, REFUSED)
  const wait = lastRequest + GAP_MS - Date.now()
  if (wait > 0) await new Promise((r) => setTimeout(r, wait))
  lastRequest = Date.now()

  const params = new URLSearchParams({ client: 'gtx', sl: 'ar', tl: 'en', dj: '1', q: text })
  for (const dt of ['t', 'bd', 'rm']) params.append('dt', dt)
  let res: Response
  try {
    res = await fetch(`${baseUrl()}/translate_a/single?${params}`, { signal: AbortSignal.timeout(TIMEOUT_MS) })
  } catch {
    throw new HttpError(502, 'Google Translate could not be reached. Check the internet connection.')
  }
  const body = await res.text()
  let raw: RawAnswer
  try {
    if (!res.ok) throw new Error(String(res.status))
    raw = JSON.parse(body) as RawAnswer
  } catch {
    // A refusal comes back as an HTML "Sorry" page or an error status.
    refusedUntil = Date.now() + COOLDOWN_MS
    throw new HttpError(503, REFUSED)
  }
  return parseAnswer(raw)
}

/** Look up a word or a line, from the cache when it has been asked before. */
export function googleLookup(text: string): Promise<GoogleLookup> {
  const q = text.replace(/\s+/g, ' ').trim()
  if (!q) return Promise.resolve({ translation: '', romanization: '', dictionary: [] })
  const cached = get<{ json: string }>('SELECT json FROM google_cache WHERE q = ?', q)
  if (cached) return Promise.resolve(JSON.parse(cached.json) as GoogleLookup)
  // One request at a time, in order.
  const next = queue.then(async () => {
    const again = get<{ json: string }>('SELECT json FROM google_cache WHERE q = ?', q)
    if (again) return JSON.parse(again.json) as GoogleLookup
    const result = await request(q)
    run(
      'INSERT OR REPLACE INTO google_cache (q, json, fetchedAt) VALUES (?, ?, ?)',
      q,
      JSON.stringify(result),
      new Date().toISOString(),
    )
    return result
  })
  queue = next.catch(() => {})
  return next
}

/** A short meaning for a word: its first dictionary senses, or the plain translation. */
export function shortMeaning(result: GoogleLookup): string {
  const terms: string[] = []
  for (const entry of result.dictionary) {
    for (const term of entry.terms) {
      if (!terms.some((t) => t.toLowerCase() === term.toLowerCase())) terms.push(term)
      if (terms.length >= 3) break
    }
    if (terms.length >= 3) break
  }
  return terms.length ? terms.join(', ') : result.translation
}

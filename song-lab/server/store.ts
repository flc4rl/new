import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import type { SQLInputValue } from 'node:sqlite'
import { all, get, run, tx, DATA_DIR, RECORDINGS_DIR } from './db'
import { HttpError } from './http'
import { splitWords } from '../shared/text'
import { schedule, startOfLocalDay, START_EASE } from '../shared/srs'
import {
  DEFAULT_SETTINGS,
  type Card,
  type CardKind,
  type CardRef,
  type CardView,
  type Direction,
  type Grade,
  type Line,
  type Question,
  type Recording,
  type Register,
  type Review,
  type ReviewQueue,
  type Settings,
  type Song,
  type SongDetail,
  type SongSummary,
  type Source,
  type Status,
  type TutorLineItem,
  type TutorSongGroup,
  type Word,
} from '../shared/types'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Row = Record<string, any>

const now = () => new Date().toISOString()

// ---- row mapping ----

const toSong = (r: Row): Song => ({
  id: r.id,
  title: r.title,
  artist: r.artist,
  youtubeId: r.youtubeId,
  savedRate: r.savedRate,
  notes: r.notes,
  createdAt: r.createdAt,
})

const toLine = (r: Row): Line => ({
  id: r.id,
  songId: r.songId,
  order: r.ord,
  arabic: r.arabic,
  translit: r.translit,
  gloss: r.gloss,
  startSec: r.startSec,
  endSec: r.endSec,
  status: r.status,
  source: r.source,
  note: r.note,
  unsure: !!r.unsure,
})

const toWord = (r: Row): Word => ({
  id: r.id,
  lineId: r.lineId,
  order: r.ord,
  arabic: r.arabic,
  translit: r.translit,
  gloss: r.gloss,
  root: r.root,
  register: r.register,
  status: r.status,
  note: r.note,
  unsure: !!r.unsure,
})

const toCard = (r: Row): Card => ({
  id: r.id,
  kind: r.kind,
  wordId: r.wordId,
  lineId: r.lineId,
  arabic: r.arabic,
  translit: r.translit,
  gloss: r.gloss,
  direction: r.direction,
  due: r.due,
  interval: r.interval,
  ease: r.ease,
  reps: r.reps,
  lapses: r.lapses,
  suspended: !!r.suspended,
  createdAt: r.createdAt,
})

const toQuestion = (r: Row): Question => ({
  id: r.id,
  lineId: r.lineId,
  wordId: r.wordId,
  text: r.text,
  resolved: !!r.resolved,
  createdAt: r.createdAt,
})

const toRecording = (r: Row): Recording => ({
  id: r.id,
  lineId: r.lineId,
  filePath: r.filePath,
  createdAt: r.createdAt,
})

function update(table: string, id: string, fields: Record<string, SQLInputValue | undefined>): void {
  const entries = Object.entries(fields).filter(([, value]) => value !== undefined)
  if (entries.length === 0) return
  const sets = entries.map(([key]) => `"${key}" = ?`).join(', ')
  run(`UPDATE ${table} SET ${sets} WHERE id = ?`, ...entries.map(([, value]) => value as SQLInputValue), id)
}

const flag = (value: boolean | undefined): number | undefined => (value === undefined ? undefined : value ? 1 : 0)

// ---- settings ----

export function getSettings(): Settings {
  const stored: Record<string, unknown> = {}
  for (const row of all<Row>('SELECT key, value FROM settings')) {
    try {
      stored[row.key] = JSON.parse(row.value)
    } catch {
      // Ignore a value that cannot be read; the default applies.
    }
  }
  return { ...DEFAULT_SETTINGS, ...stored } as Settings
}

export function saveSettings(patch: Partial<Settings>): Settings {
  tx(() => {
    for (const [key, value] of Object.entries(patch)) {
      if (value === undefined) continue
      run(
        'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        key,
        JSON.stringify(value),
      )
    }
  })
  return getSettings()
}

// ---- songs ----

export function listSongs(): SongSummary[] {
  const rows = all<Row>(`
    SELECT s.*,
      (SELECT COUNT(*) FROM lines l WHERE l.songId = s.id) AS lineCount,
      (SELECT COUNT(*) FROM lines l WHERE l.songId = s.id AND l.startSec IS NOT NULL) AS syncedCount,
      (SELECT COUNT(*) FROM lines l WHERE l.songId = s.id AND l.status = 'verified') AS verifiedCount,
      (SELECT COUNT(*) FROM cards c JOIN lines l ON l.id = c.lineId WHERE l.songId = s.id) AS cardCount
    FROM songs s
    ORDER BY s.createdAt DESC`)
  return rows.map((r) => ({
    ...toSong(r),
    lineCount: r.lineCount,
    syncedCount: r.syncedCount,
    verifiedCount: r.verifiedCount,
    cardCount: r.cardCount,
  }))
}

export function getSong(id: string): Song {
  const row = get<Row>('SELECT * FROM songs WHERE id = ?', id)
  if (!row) throw new HttpError(404, 'That song is not in the library.')
  return toSong(row)
}

export function getSongDetail(id: string): SongDetail {
  const song = getSong(id)
  const lines = linesOfSong(id)
  const words = all<Row>(
    `SELECT w.* FROM words w JOIN lines l ON l.id = w.lineId WHERE l.songId = ? ORDER BY l.ord, w.ord`,
    id,
  ).map(toWord)
  const questions = all<Row>(
    `SELECT q.* FROM questions q JOIN lines l ON l.id = q.lineId WHERE l.songId = ? ORDER BY q.createdAt`,
    id,
  ).map(toQuestion)
  const recordings = all<Row>(
    `SELECT r.* FROM recordings r JOIN lines l ON l.id = r.lineId WHERE l.songId = ?`,
    id,
  ).map(toRecording)
  const cards = all<Row>(
    `SELECT c.id, c.kind, c.wordId, c.lineId, c.direction FROM cards c JOIN lines l ON l.id = c.lineId WHERE l.songId = ?`,
    id,
  ) as CardRef[]
  return { song, lines, words, questions, recordings, cards: cards.map((c) => ({ ...c })) }
}

export function createSong(input: { title: string; artist: string; youtubeId: string; notes: string }): Song {
  const id = randomUUID()
  run(
    'INSERT INTO songs (id, title, artist, youtubeId, savedRate, notes, createdAt) VALUES (?, ?, ?, ?, 1, ?, ?)',
    id,
    input.title,
    input.artist,
    input.youtubeId,
    input.notes,
    now(),
  )
  return getSong(id)
}

export function updateSong(
  id: string,
  patch: Partial<Pick<Song, 'title' | 'artist' | 'notes' | 'savedRate' | 'youtubeId'>>,
): Song {
  getSong(id)
  update('songs', id, patch)
  return getSong(id)
}

/** Delete a song with its lines, words, recordings and questions. Its cards are kept as stand-alone cards or deleted. */
export function deleteSong(id: string, keepCards: boolean): void {
  getSong(id)
  tx(() => {
    const cardIds = all<Row>(
      'SELECT c.id FROM cards c JOIN lines l ON l.id = c.lineId WHERE l.songId = ?',
      id,
    ).map((r) => r.id as string)
    if (keepCards) cardIds.forEach((cardId) => detachCard(cardId, true))
    else cardIds.forEach((cardId) => run('DELETE FROM cards WHERE id = ?', cardId))
    for (const rec of all<Row>(
      'SELECT r.filePath FROM recordings r JOIN lines l ON l.id = r.lineId WHERE l.songId = ?',
      id,
    )) {
      removeFile(rec.filePath)
    }
    run('DELETE FROM songs WHERE id = ?', id)
  })
}

// ---- lines ----

export function linesOfSong(songId: string): Line[] {
  return all<Row>('SELECT * FROM lines WHERE songId = ? ORDER BY ord', songId).map(toLine)
}

export function getLine(id: string): Line {
  const row = get<Row>('SELECT * FROM lines WHERE id = ?', id)
  if (!row) throw new HttpError(404, 'That line no longer exists.')
  return toLine(row)
}

function renumberLines(songId: string): void {
  linesOfSong(songId).forEach((line, i) => {
    if (line.order !== i) run('UPDATE lines SET ord = ? WHERE id = ?', i, line.id)
  })
}

/** Add lyric lines typed or pasted by the user, after a given line or at the end. */
export function addLines(songId: string, rows: string[], afterLineId?: string | null): Line[] {
  getSong(songId)
  return tx(() => {
    const existing = linesOfSong(songId)
    let insertAt = existing.length
    if (afterLineId) {
      const index = existing.findIndex((l) => l.id === afterLineId)
      if (index >= 0) insertAt = index + 1
    } else if (afterLineId === null) {
      insertAt = 0
    }
    // Make room, then insert.
    for (const line of existing.slice(insertAt).reverse()) {
      run('UPDATE lines SET ord = ? WHERE id = ?', line.order + rows.length, line.id)
    }
    rows.forEach((arabic, i) => {
      run('INSERT INTO lines (id, songId, ord, arabic) VALUES (?, ?, ?, ?)', randomUUID(), songId, insertAt + i, arabic)
    })
    renumberLines(songId)
    return linesOfSong(songId)
  })
}

export interface LinePatch {
  arabic?: string
  translit?: string
  gloss?: string
  startSec?: number | null
  endSec?: number | null
  status?: Status
  source?: Source
  note?: string
  unsure?: boolean
}

export function updateLine(id: string, patch: LinePatch): Line {
  const current = getLine(id)
  let status = patch.status
  // A verified line goes back to draft only when its Arabic changes.
  if (status === undefined && current.status === 'verified' && patch.arabic !== undefined && patch.arabic !== current.arabic) {
    status = 'draft'
  }
  update('lines', id, {
    arabic: patch.arabic,
    translit: patch.translit,
    gloss: patch.gloss,
    startSec: patch.startSec,
    endSec: patch.endSec,
    status,
    source: patch.source,
    note: patch.note,
    // Confirming a line clears Claude's doubt about it.
    unsure: status === 'verified' ? 0 : flag(patch.unsure),
  })
  return getLine(id)
}

export function deleteLine(id: string): void {
  const line = getLine(id)
  tx(() => {
    for (const card of all<Row>('SELECT id FROM cards WHERE lineId = ?', id)) detachCard(card.id, true)
    const rec = get<Row>('SELECT filePath FROM recordings WHERE lineId = ?', id)
    if (rec) removeFile(rec.filePath)
    run('DELETE FROM lines WHERE id = ?', id)
    renumberLines(line.songId)
  })
}

export function moveLine(id: string, delta: -1 | 1): Line[] {
  const line = getLine(id)
  return tx(() => {
    const lines = linesOfSong(line.songId)
    const index = lines.findIndex((l) => l.id === id)
    const other = lines[index + delta]
    if (other) {
      run('UPDATE lines SET ord = ? WHERE id = ?', other.order, line.id)
      run('UPDATE lines SET ord = ? WHERE id = ?', line.order, other.id)
    }
    return linesOfSong(line.songId)
  })
}

export function clearTimings(songId: string): void {
  getSong(songId)
  run('UPDATE lines SET startSec = NULL, endSec = NULL WHERE songId = ?', songId)
}

// ---- words ----

export function wordsOfLine(lineId: string): Word[] {
  return all<Row>('SELECT * FROM words WHERE lineId = ? ORDER BY ord', lineId).map(toWord)
}

export function getWord(id: string): Word {
  const row = get<Row>('SELECT * FROM words WHERE id = ?', id)
  if (!row) throw new HttpError(404, 'That word no longer exists.')
  return toWord(row)
}

function renumberWords(lineId: string): void {
  wordsOfLine(lineId).forEach((word, i) => {
    if (word.order !== i) run('UPDATE words SET ord = ? WHERE id = ?', i, word.id)
  })
}

/**
 * Split a line's Arabic into words. Words that already exist and still match
 * are kept with their transliteration, status and cards; the rest are replaced.
 */
export function splitLine(lineId: string): Word[] {
  const line = getLine(lineId)
  return tx(() => {
    const tokens = splitWords(line.arabic)
    const existing = wordsOfLine(lineId)
    const used = new Set<string>()
    tokens.forEach((token, position) => {
      const match = existing.find((w) => !used.has(w.id) && w.arabic === token)
      if (match) {
        used.add(match.id)
        run('UPDATE words SET ord = ? WHERE id = ?', position, match.id)
      } else {
        run('INSERT INTO words (id, lineId, ord, arabic) VALUES (?, ?, ?, ?)', randomUUID(), lineId, position, token)
      }
    })
    for (const word of existing) {
      if (!used.has(word.id)) removeWord(word.id)
    }
    return wordsOfLine(lineId)
  })
}

export function addWord(lineId: string, arabic: string): Word {
  getLine(lineId)
  const id = randomUUID()
  const count = get<Row>('SELECT COUNT(*) AS n FROM words WHERE lineId = ?', lineId)!.n as number
  run('INSERT INTO words (id, lineId, ord, arabic) VALUES (?, ?, ?, ?)', id, lineId, count, arabic)
  return getWord(id)
}

export interface WordPatch {
  arabic?: string
  translit?: string
  gloss?: string
  root?: string
  register?: Register
  status?: Status
  note?: string
  unsure?: boolean
}

export function updateWord(id: string, patch: WordPatch): Word {
  const current = getWord(id)
  let status = patch.status
  if (status === undefined && current.status === 'verified' && patch.arabic !== undefined && patch.arabic !== current.arabic) {
    status = 'draft'
  }
  update('words', id, {
    arabic: patch.arabic,
    translit: patch.translit,
    gloss: patch.gloss,
    root: patch.root,
    register: patch.register,
    status,
    note: patch.note,
    unsure: status === 'verified' ? 0 : flag(patch.unsure),
  })
  return getWord(id)
}

function removeWord(id: string): void {
  for (const card of all<Row>('SELECT id FROM cards WHERE wordId = ?', id)) detachCard(card.id, false)
  run('DELETE FROM words WHERE id = ?', id)
}

export function deleteWord(id: string): void {
  const word = getWord(id)
  tx(() => {
    removeWord(id)
    renumberWords(word.lineId)
  })
}

// ---- cards ----

const CARD_VIEW_SQL = `
  SELECT c.*,
    w.arabic AS wArabic, w.translit AS wTranslit, w.gloss AS wGloss, w.status AS wStatus,
    l.arabic AS lArabic, l.translit AS lTranslit, l.gloss AS lGloss, l.status AS lStatus,
    l.startSec AS lStart, l.endSec AS lEnd,
    s.id AS songId, s.title AS songTitle, s.youtubeId AS youtubeId, s.savedRate AS savedRate,
    NOT EXISTS (SELECT 1 FROM reviews r WHERE r.cardId = c.id) AS isNew
  FROM cards c
  LEFT JOIN words w ON w.id = c.wordId
  LEFT JOIN lines l ON l.id = c.lineId
  LEFT JOIN songs s ON s.id = l.songId`

/** If a line has no end time yet, play this long. */
const FALLBACK_CLIP_SEC = 6

function toCardView(r: Row): CardView {
  const card = toCard(r)
  let status: Status | null = null
  let lineArabic: string | null = null
  // A song card reads its text from the word or line it points to.
  if (card.kind === 'word' && r.wArabic != null) {
    card.arabic = r.wArabic
    card.translit = r.wTranslit
    card.gloss = r.wGloss
    status = r.wStatus
    lineArabic = r.lArabic ?? null
  } else if (card.kind === 'line' && r.lArabic != null) {
    card.arabic = r.lArabic
    card.translit = r.lTranslit
    card.gloss = r.lGloss
    status = r.lStatus
  }
  const clip =
    r.youtubeId && r.lStart != null
      ? {
          youtubeId: r.youtubeId as string,
          startSec: r.lStart as number,
          endSec: (r.lEnd as number | null) ?? (r.lStart as number) + FALLBACK_CLIP_SEC,
          rate: r.savedRate as number,
        }
      : null
  return {
    ...card,
    isNew: !!r.isNew,
    songId: r.songId ?? null,
    songTitle: r.songTitle ?? null,
    clip,
    lineArabic,
    status,
  }
}

export function getCardView(id: string): CardView {
  const row = get<Row>(`${CARD_VIEW_SQL} WHERE c.id = ?`, id)
  if (!row) throw new HttpError(404, 'That card no longer exists.')
  return toCardView(row)
}

export function listCards(): CardView[] {
  return all<Row>(`${CARD_VIEW_SQL} ORDER BY c.createdAt DESC, c.direction`).map(toCardView)
}

/**
 * Copy a card's text into the card itself, so it survives the deletion of the
 * word or line it was made from. With dropLine the card also loses its clip.
 */
function detachCard(id: string, dropLine: boolean): void {
  const view = getCardView(id)
  update('cards', id, {
    arabic: view.arabic,
    translit: view.translit,
    gloss: view.gloss,
    wordId: null,
    lineId: dropLine ? null : view.lineId,
  })
}

export type NewCardInput =
  | { kind: 'word'; wordId: string }
  | { kind: 'line'; lineId: string }
  | { kind: 'phrase'; arabic: string; translit: string; gloss: string }

function directionsFor(setting: Settings['cardDirections']): Direction[] {
  return setting === 'both' ? ['ar-en', 'en-ar'] : [setting]
}

/** Make cards for a word, a line or a hand-typed phrase. Directions that already exist are skipped. */
export function createCards(input: NewCardInput, directions?: Direction[]): CardView[] {
  const wanted = directions?.length ? directions : directionsFor(getSettings().cardDirections)
  return tx(() => {
    let wordId: string | null = null
    let lineId: string | null = null
    let text = { arabic: '', translit: '', gloss: '' }
    if (input.kind === 'word') {
      const word = getWord(input.wordId)
      wordId = word.id
      lineId = word.lineId
    } else if (input.kind === 'line') {
      lineId = getLine(input.lineId).id
    } else {
      text = { arabic: input.arabic, translit: input.translit, gloss: input.gloss }
    }
    const created: CardView[] = []
    const stamp = now()
    for (const direction of wanted) {
      const duplicate =
        input.kind === 'word'
          ? get<Row>(`SELECT id FROM cards WHERE kind = 'word' AND wordId = ? AND direction = ?`, wordId, direction)
          : input.kind === 'line'
            ? get<Row>(`SELECT id FROM cards WHERE kind = 'line' AND lineId = ? AND direction = ?`, lineId, direction)
            : get<Row>(
                `SELECT id FROM cards WHERE kind = 'phrase' AND arabic = ? AND gloss = ? AND direction = ?`,
                text.arabic,
                text.gloss,
                direction,
              )
      if (duplicate) continue
      const id = randomUUID()
      run(
        `INSERT INTO cards (id, kind, wordId, lineId, arabic, translit, gloss, direction, due, interval, ease, reps, lapses, suspended, createdAt)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, ?)`,
        id,
        input.kind,
        wordId,
        lineId,
        text.arabic,
        text.translit,
        text.gloss,
        direction,
        stamp,
        START_EASE,
        stamp,
      )
      created.push(getCardView(id))
    }
    return created
  })
}

export function updateCard(
  id: string,
  patch: { suspended?: boolean; arabic?: string; translit?: string; gloss?: string },
): CardView {
  getCardView(id)
  update('cards', id, {
    suspended: flag(patch.suspended),
    arabic: patch.arabic,
    translit: patch.translit,
    gloss: patch.gloss,
  })
  return getCardView(id)
}

export function deleteCard(id: string): void {
  getCardView(id)
  run('DELETE FROM cards WHERE id = ?', id)
}

/** Both directions of one item share a key, so they are not introduced on the same day. */
function siblingKey(card: CardView): string {
  const item = card.wordId ?? (card.kind === 'line' ? card.lineId : null)
  return `${card.kind}:${item ?? `${card.arabic}|${card.gloss}`}`
}

/** A card still in its learning steps counts as due if its step ends within this window. */
const LEARNING_WINDOW_MS = 10 * 60_000

export function reviewQueue(at = new Date()): ReviewQueue {
  const settings = getSettings()
  const dueLimit = at.toISOString()
  const learningLimit = new Date(at.getTime() + LEARNING_WINDOW_MS).toISOString()
  const due = all<Row>(
    `${CARD_VIEW_SQL}
     WHERE c.suspended = 0
       AND EXISTS (SELECT 1 FROM reviews r WHERE r.cardId = c.id)
       AND (c.due <= ? OR (c.reps = 0 AND c.due <= ?))
     ORDER BY c.due`,
    dueLimit,
    learningLimit,
  ).map(toCardView)

  const dayStart = startOfLocalDay(at).toISOString()
  const introducedToday = all<Row>(
    `${CARD_VIEW_SQL}
     WHERE (SELECT MIN(r.reviewedAt) FROM reviews r WHERE r.cardId = c.id) >= ?`,
    dayStart,
  ).map(toCardView)
  const newLeftToday = Math.max(0, settings.newPerDay - introducedToday.length)

  const allNew = all<Row>(
    `${CARD_VIEW_SQL}
     WHERE c.suspended = 0 AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.cardId = c.id)
     ORDER BY c.createdAt, c.direction`,
  ).map(toCardView)
  const seen = new Set(introducedToday.map(siblingKey))
  const fresh: CardView[] = []
  for (const card of allNew) {
    if (fresh.length >= newLeftToday) break
    const key = siblingKey(card)
    if (seen.has(key)) continue
    seen.add(key)
    fresh.push(card)
  }

  const totalCards = get<Row>('SELECT COUNT(*) AS n FROM cards')!.n as number
  return {
    cards: [...due, ...fresh],
    dueCount: due.length,
    newCount: fresh.length,
    newLeftToday,
    totalCards,
  }
}

export function gradeCard(id: string, grade: Grade, at = new Date()): { card: CardView; requeue: boolean } {
  const card = getCardView(id)
  const next = schedule(card, grade, at)
  tx(() => {
    update('cards', id, {
      due: next.due.toISOString(),
      interval: next.interval,
      ease: next.ease,
      reps: next.reps,
      lapses: next.lapses,
    })
    run('INSERT INTO reviews (id, cardId, reviewedAt, grade) VALUES (?, ?, ?, ?)', randomUUID(), id, at.toISOString(), grade)
  })
  return { card: getCardView(id), requeue: next.requeue }
}

// ---- questions ----

export function getQuestion(id: string): Question {
  const row = get<Row>('SELECT * FROM questions WHERE id = ?', id)
  if (!row) throw new HttpError(404, 'That question no longer exists.')
  return toQuestion(row)
}

export function addQuestion(input: { lineId?: string | null; wordId?: string | null; text: string }): Question {
  let lineId = input.lineId ?? null
  const wordId = input.wordId ?? null
  if (wordId) lineId = getWord(wordId).lineId
  else if (lineId) getLine(lineId)
  else throw new HttpError(400, 'A question belongs to a line or a word.')
  const id = randomUUID()
  run(
    'INSERT INTO questions (id, lineId, wordId, text, resolved, createdAt) VALUES (?, ?, ?, ?, 0, ?)',
    id,
    lineId,
    wordId,
    input.text,
    now(),
  )
  return getQuestion(id)
}

export function updateQuestion(id: string, patch: { text?: string; resolved?: boolean }): Question {
  getQuestion(id)
  update('questions', id, { text: patch.text, resolved: flag(patch.resolved) })
  return getQuestion(id)
}

export function deleteQuestion(id: string): void {
  getQuestion(id)
  run('DELETE FROM questions WHERE id = ?', id)
}

// ---- recordings ----

const AUDIO_EXTENSIONS: Record<string, string> = {
  'audio/webm': 'webm',
  'video/webm': 'webm',
  'audio/ogg': 'ogg',
  'audio/mp4': 'm4a',
  'audio/aac': 'aac',
  'audio/mpeg': 'mp3',
  'audio/wav': 'wav',
  'audio/x-wav': 'wav',
}

const AUDIO_TYPES: Record<string, string> = {
  webm: 'audio/webm',
  ogg: 'audio/ogg',
  m4a: 'audio/mp4',
  aac: 'audio/aac',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
}

function removeFile(relativePath: string): void {
  const full = path.resolve(DATA_DIR, relativePath)
  if (!full.startsWith(RECORDINGS_DIR + path.sep)) return
  fs.rmSync(full, { force: true })
}

export function audioExtension(contentType: string): string {
  const base = contentType.split(';')[0].trim().toLowerCase()
  const ext = AUDIO_EXTENSIONS[base]
  if (!ext) throw new HttpError(415, 'That audio format is not supported.')
  return ext
}

/** Keep the latest take for a line, replacing any earlier one. */
export function saveRecording(lineId: string, data: Buffer, extension: string): Recording {
  getLine(lineId)
  if (!AUDIO_TYPES[extension]) throw new HttpError(415, 'That audio format is not supported.')
  if (data.length === 0) throw new HttpError(400, 'The recording is empty.')
  const previous = get<Row>('SELECT * FROM recordings WHERE lineId = ?', lineId)
  if (previous) removeFile(previous.filePath)
  fs.mkdirSync(RECORDINGS_DIR, { recursive: true })
  const filePath = `recordings/${lineId}.${extension}`
  fs.writeFileSync(path.join(DATA_DIR, filePath), data)
  const id = previous?.id ?? randomUUID()
  run(
    `INSERT INTO recordings (id, lineId, filePath, createdAt) VALUES (?, ?, ?, ?)
     ON CONFLICT(lineId) DO UPDATE SET filePath = excluded.filePath, createdAt = excluded.createdAt`,
    id,
    lineId,
    filePath,
    now(),
  )
  return toRecording(get<Row>('SELECT * FROM recordings WHERE lineId = ?', lineId)!)
}

export function recordingFile(lineId: string): { fullPath: string; contentType: string } {
  const row = get<Row>('SELECT * FROM recordings WHERE lineId = ?', lineId)
  const fullPath = row ? path.resolve(DATA_DIR, row.filePath) : ''
  if (!row || !fullPath.startsWith(RECORDINGS_DIR + path.sep) || !fs.existsSync(fullPath)) {
    throw new HttpError(404, 'There is no recording for that line.')
  }
  const ext = path.extname(fullPath).slice(1)
  return { fullPath, contentType: AUDIO_TYPES[ext] ?? 'application/octet-stream' }
}

export function deleteRecording(lineId: string): void {
  const row = get<Row>('SELECT * FROM recordings WHERE lineId = ?', lineId)
  if (!row) return
  removeFile(row.filePath)
  run('DELETE FROM recordings WHERE lineId = ?', lineId)
}

// ---- tutor queue ----

/** Every draft line, draft word and open question, grouped by song and in song order. */
export function tutorQueue(): TutorSongGroup[] {
  const songs = all<Row>('SELECT * FROM songs ORDER BY createdAt').map(toSong)
  const groups: TutorSongGroup[] = []
  for (const song of songs) {
    const detail = getSongDetail(song.id)
    const open = detail.questions.filter((q) => !q.resolved)
    const items: TutorLineItem[] = []
    for (const line of detail.lines) {
      const words = detail.words
        .filter((w) => w.lineId === line.id)
        .map((word) => ({ word, questions: open.filter((q) => q.wordId === word.id) }))
        .filter((item) => item.word.status === 'draft' || item.questions.length > 0)
      const questions = open.filter((q) => q.lineId === line.id && !q.wordId)
      const lineIsDraft = line.status === 'draft'
      if (lineIsDraft || words.length > 0 || questions.length > 0) {
        items.push({ line, lineIsDraft, words, questions })
      }
    }
    if (items.length > 0) groups.push({ song, lines: items })
  }
  return groups
}

// ---- backup ----

export interface Backup {
  app: 'sudanese-arabic-song-lab'
  version: 1
  exportedAt: string
  settings: Partial<Settings>
  songs: Song[]
  lines: Line[]
  words: Word[]
  cards: Card[]
  reviews: Review[]
  questions: Question[]
  recordings: (Recording & { dataBase64?: string })[]
}

export function exportBackup(includeAudio: boolean): Backup {
  const recordings = all<Row>('SELECT * FROM recordings').map((r) => {
    const rec: Recording & { dataBase64?: string } = toRecording(r)
    if (includeAudio) {
      const full = path.resolve(DATA_DIR, rec.filePath)
      if (full.startsWith(RECORDINGS_DIR + path.sep) && fs.existsSync(full)) {
        rec.dataBase64 = fs.readFileSync(full).toString('base64')
      }
    }
    return rec
  })
  return {
    app: 'sudanese-arabic-song-lab',
    version: 1,
    exportedAt: now(),
    settings: getSettings(),
    songs: all<Row>('SELECT * FROM songs ORDER BY createdAt').map(toSong),
    lines: all<Row>('SELECT * FROM lines ORDER BY songId, ord').map(toLine),
    words: all<Row>('SELECT * FROM words ORDER BY lineId, ord').map(toWord),
    cards: all<Row>('SELECT * FROM cards ORDER BY createdAt').map(toCard),
    reviews: all<Row>('SELECT * FROM reviews ORDER BY reviewedAt').map((r) => ({
      id: r.id,
      cardId: r.cardId,
      reviewedAt: r.reviewedAt,
      grade: r.grade as Grade,
    })),
    questions: all<Row>('SELECT * FROM questions ORDER BY createdAt').map(toQuestion),
    recordings,
  }
}

/** Replace everything in the app with the contents of a backup. */
export function importBackup(backup: Backup): Record<string, number> {
  const songIds = new Set(backup.songs.map((s) => s.id))
  const lines = backup.lines.filter((l) => songIds.has(l.songId))
  const lineIds = new Set(lines.map((l) => l.id))
  const words = backup.words.filter((w) => lineIds.has(w.lineId))
  const wordIds = new Set(words.map((w) => w.id))
  const cardIds = new Set(backup.cards.map((c) => c.id))
  const reviews = backup.reviews.filter((r) => cardIds.has(r.cardId))
  const questions = backup.questions.filter(
    (q) => (q.lineId == null || lineIds.has(q.lineId)) && (q.wordId == null || wordIds.has(q.wordId)),
  )
  const recordings = backup.recordings.filter((r) => lineIds.has(r.lineId))

  tx(() => {
    for (const table of ['reviews', 'cards', 'questions', 'recordings', 'words', 'lines', 'songs', 'settings']) {
      run(`DELETE FROM ${table}`)
    }
    for (const s of backup.songs) {
      run(
        'INSERT INTO songs (id, title, artist, youtubeId, savedRate, notes, createdAt) VALUES (?, ?, ?, ?, ?, ?, ?)',
        s.id, s.title, s.artist, s.youtubeId, s.savedRate, s.notes, s.createdAt,
      )
    }
    for (const l of lines) {
      run(
        `INSERT INTO lines (id, songId, ord, arabic, translit, gloss, startSec, endSec, status, source, note, unsure)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        l.id, l.songId, l.order, l.arabic, l.translit, l.gloss, l.startSec, l.endSec, l.status, l.source, l.note,
        l.unsure ? 1 : 0,
      )
    }
    for (const w of words) {
      run(
        `INSERT INTO words (id, lineId, ord, arabic, translit, gloss, root, register, status, note, unsure)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        w.id, w.lineId, w.order, w.arabic, w.translit, w.gloss, w.root, w.register, w.status, w.note, w.unsure ? 1 : 0,
      )
    }
    for (const c of backup.cards) {
      run(
        `INSERT INTO cards (id, kind, wordId, lineId, arabic, translit, gloss, direction, due, interval, ease, reps, lapses, suspended, createdAt)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        c.id, c.kind as CardKind,
        c.wordId && wordIds.has(c.wordId) ? c.wordId : null,
        c.lineId && lineIds.has(c.lineId) ? c.lineId : null,
        c.arabic, c.translit, c.gloss, c.direction, c.due, c.interval, c.ease, c.reps, c.lapses, c.suspended ? 1 : 0,
        c.createdAt,
      )
    }
    for (const r of reviews) {
      run('INSERT INTO reviews (id, cardId, reviewedAt, grade) VALUES (?, ?, ?, ?)', r.id, r.cardId, r.reviewedAt, r.grade)
    }
    for (const q of questions) {
      run(
        'INSERT INTO questions (id, lineId, wordId, text, resolved, createdAt) VALUES (?, ?, ?, ?, ?, ?)',
        q.id, q.lineId, q.wordId, q.text, q.resolved ? 1 : 0, q.createdAt,
      )
    }
    // Recordings: clear the folder, then write back the takes that came with audio.
    fs.rmSync(RECORDINGS_DIR, { recursive: true, force: true })
    fs.mkdirSync(RECORDINGS_DIR, { recursive: true })
    for (const rec of recordings) {
      if (!rec.dataBase64) continue
      const ext = path.extname(rec.filePath).slice(1)
      if (!AUDIO_TYPES[ext]) continue
      const filePath = `recordings/${rec.lineId}.${ext}`
      fs.writeFileSync(path.join(DATA_DIR, filePath), Buffer.from(rec.dataBase64, 'base64'))
      run('INSERT INTO recordings (id, lineId, filePath, createdAt) VALUES (?, ?, ?, ?)', rec.id, rec.lineId, filePath, rec.createdAt)
    }
    for (const [key, value] of Object.entries(backup.settings ?? {})) {
      if (key in DEFAULT_SETTINGS && value !== undefined) {
        run('INSERT INTO settings (key, value) VALUES (?, ?)', key, JSON.stringify(value))
      }
    }
  })
  return {
    songs: backup.songs.length,
    lines: lines.length,
    words: words.length,
    cards: backup.cards.length,
    reviews: reviews.length,
    questions: questions.length,
    recordings: recordings.filter((r) => r.dataBase64).length,
  }
}

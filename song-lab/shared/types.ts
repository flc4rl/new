// Record types shared by the server and the browser.
// All times are seconds from the start of the video; all dates are ISO strings.

export type Status = 'draft' | 'verified'
export type Source = 'user' | 'claude' | 'tutor'
export type Register = 'everyday' | 'poetic' | 'unknown'
export type CardKind = 'word' | 'line' | 'phrase'
export type Direction = 'ar-en' | 'en-ar'
export type Grade = 'again' | 'hard' | 'good' | 'easy'

export const GRADES: Grade[] = ['again', 'hard', 'good', 'easy']
export const REGISTERS: Register[] = ['everyday', 'poetic', 'unknown']

export interface Song {
  id: string
  title: string
  artist: string
  youtubeId: string
  savedRate: number
  notes: string
  createdAt: string
}

export interface Line {
  id: string
  songId: string
  order: number
  arabic: string
  translit: string
  gloss: string
  startSec: number | null
  endSec: number | null
  status: Status
  source: Source
  note: string
  /** Set by a Claude draft when it could not be confident about the line. */
  unsure: boolean
}

export interface Word {
  id: string
  lineId: string
  order: number
  arabic: string
  translit: string
  gloss: string
  root: string
  register: Register
  status: Status
  note: string
  /** The exact words in the line's English gloss that translate this word, for linking them on hover. */
  glossPart: string
  /** Set by a Claude draft when it could not be confident about the word. */
  unsure: boolean
}

export interface Card {
  id: string
  kind: CardKind
  wordId: string | null
  lineId: string | null
  /** Own text. Only used by hand-made phrase cards and cards whose song was deleted. */
  arabic: string
  translit: string
  gloss: string
  direction: Direction
  due: string
  /** Days until the next review once the card has graduated; 0 while learning. */
  interval: number
  ease: number
  reps: number
  lapses: number
  suspended: boolean
  createdAt: string
}

export interface Review {
  id: string
  cardId: string
  reviewedAt: string
  grade: Grade
}

export interface Recording {
  id: string
  lineId: string
  filePath: string
  createdAt: string
}

export interface Question {
  id: string
  lineId: string | null
  wordId: string | null
  text: string
  resolved: boolean
  createdAt: string
}

export interface Settings {
  /** Daily limit on new cards. */
  newPerDay: number
  /** Which directions a click on "make card" creates. */
  cardDirections: 'both' | Direction
  /** Seconds of real time subtracted from a sync tap to cancel out reaction time. */
  tapOffsetSec: number
  /** Seconds a loop starts before the line, so the first syllable is not clipped. */
  loopLeadInSec: number
  /** How many times a line loop plays; 0 means until stopped. */
  loopRepeats: number
  /** Seconds of silence between repeats. */
  loopPauseSec: number
  /** Translate pasted lyrics with Claude straight away, when a key is set. */
  autoDraft: boolean
}

export const DEFAULT_SETTINGS: Settings = {
  newPerDay: 10,
  cardDirections: 'both',
  tapOffsetSec: 0.15,
  loopLeadInSec: 0.2,
  loopRepeats: 5,
  loopPauseSec: 1,
  autoDraft: true,
}

// ---- API payloads ----

export interface SongSummary extends Song {
  lineCount: number
  syncedCount: number
  verifiedCount: number
  cardCount: number
}

export interface CardRef {
  id: string
  kind: CardKind
  wordId: string | null
  lineId: string | null
  direction: Direction
}

export interface SongDetail {
  song: Song
  lines: Line[]
  words: Word[]
  questions: Question[]
  recordings: Recording[]
  cards: CardRef[]
}

export interface Clip {
  youtubeId: string
  startSec: number
  endSec: number
  rate: number
}

/** A card with its text resolved from the word or line it points to. */
export interface CardView extends Card {
  isNew: boolean
  songId: string | null
  songTitle: string | null
  clip: Clip | null
  /** For a word card: the line the word comes from, shown with the answer. */
  lineArabic: string | null
  /** Whether the word or line behind the card has been confirmed; null for hand-made phrases. */
  status: Status | null
}

export interface ReviewQueue {
  cards: CardView[]
  dueCount: number
  newCount: number
  newLeftToday: number
  totalCards: number
}

export interface TranslitLetter {
  arabic: string
  write: string
  sound: string
}

export interface TranslitConfig {
  dialect: string
  rules: string[]
  letters: TranslitLetter[]
  otherConsonants: string
}

export interface Meta {
  /** True when the server has an API key, so "Draft with Claude" can be offered. */
  claude: boolean
  model: string
  translit: TranslitConfig
}

export interface TutorWordItem {
  word: Word
  questions: Question[]
}

export interface TutorLineItem {
  line: Line
  /** True when the line itself is still a draft (it may also be listed only for its words or questions). */
  lineIsDraft: boolean
  words: TutorWordItem[]
  questions: Question[]
}

export interface TutorSongGroup {
  song: Song
  lines: TutorLineItem[]
}

export interface DraftResult {
  /** Lines that received at least one new field. */
  updatedLines: number
  updatedWords: number
  /** Human-readable reasons for anything that was skipped. */
  skipped: string[]
}

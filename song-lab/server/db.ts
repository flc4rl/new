import { DatabaseSync, type SQLInputValue } from 'node:sqlite'
import fs from 'node:fs'
import path from 'node:path'
import { ROOT } from './config'

// Everything the learner creates lives under ./data, which git ignores.
export const DATA_DIR = path.resolve(ROOT, process.env.SONGLAB_DATA_DIR ?? 'data')
export const RECORDINGS_DIR = path.join(DATA_DIR, 'recordings')
const DB_PATH = path.join(DATA_DIR, 'songlab.sqlite')

// "order" is a reserved word in SQL, so the column is called ord.
const SCHEMA = `
CREATE TABLE IF NOT EXISTS songs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  artist TEXT NOT NULL DEFAULT '',
  youtubeId TEXT NOT NULL,
  savedRate REAL NOT NULL DEFAULT 1,
  notes TEXT NOT NULL DEFAULT '',
  createdAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lines (
  id TEXT PRIMARY KEY,
  songId TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  arabic TEXT NOT NULL DEFAULT '',
  translit TEXT NOT NULL DEFAULT '',
  gloss TEXT NOT NULL DEFAULT '',
  startSec REAL,
  endSec REAL,
  status TEXT NOT NULL DEFAULT 'draft',
  source TEXT NOT NULL DEFAULT 'user',
  note TEXT NOT NULL DEFAULT '',
  unsure INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lines_song ON lines(songId, ord);
CREATE TABLE IF NOT EXISTS words (
  id TEXT PRIMARY KEY,
  lineId TEXT NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  arabic TEXT NOT NULL DEFAULT '',
  translit TEXT NOT NULL DEFAULT '',
  gloss TEXT NOT NULL DEFAULT '',
  root TEXT NOT NULL DEFAULT '',
  register TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL DEFAULT 'draft',
  note TEXT NOT NULL DEFAULT '',
  unsure INTEGER NOT NULL DEFAULT 0,
  glossPart TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS words_line ON words(lineId, ord);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  wordId TEXT REFERENCES words(id) ON DELETE SET NULL,
  lineId TEXT REFERENCES lines(id) ON DELETE SET NULL,
  arabic TEXT NOT NULL DEFAULT '',
  translit TEXT NOT NULL DEFAULT '',
  gloss TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL,
  due TEXT NOT NULL,
  interval REAL NOT NULL DEFAULT 0,
  ease REAL NOT NULL DEFAULT 2.5,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  suspended INTEGER NOT NULL DEFAULT 0,
  createdAt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_due ON cards(due);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  cardId TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  reviewedAt TEXT NOT NULL,
  grade TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_card ON reviews(cardId, reviewedAt);
CREATE TABLE IF NOT EXISTS recordings (
  id TEXT PRIMARY KEY,
  lineId TEXT NOT NULL UNIQUE REFERENCES lines(id) ON DELETE CASCADE,
  filePath TEXT NOT NULL,
  createdAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  lineId TEXT REFERENCES lines(id) ON DELETE CASCADE,
  wordId TEXT REFERENCES words(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  createdAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
`

let db: DatabaseSync | null = null
let txDepth = 0

/**
 * Open the database on first use. If the data folder was wiped while the app
 * was running, start again from an empty database instead of writing into a
 * file that no longer exists on disk.
 */
export function getDb(): DatabaseSync {
  if (db && txDepth === 0 && !fs.existsSync(DB_PATH)) {
    try {
      db.close()
    } catch {
      // The handle is already unusable; a new one is opened below.
    }
    db = null
  }
  if (!db) {
    fs.mkdirSync(RECORDINGS_DIR, { recursive: true })
    db = new DatabaseSync(DB_PATH)
    db.exec('PRAGMA foreign_keys = ON;')
    db.exec(SCHEMA)
    migrate(db)
  }
  return db
}

/** Bring a database made by an earlier version up to date. */
function migrate(d: DatabaseSync): void {
  const wordColumns = (d.prepare('PRAGMA table_info(words)').all() as { name: string }[]).map((c) => c.name)
  if (!wordColumns.includes('glossPart')) d.exec("ALTER TABLE words ADD COLUMN glossPart TEXT NOT NULL DEFAULT ''")
}

export function closeDb(): void {
  db?.close()
  db = null
}

type Params = SQLInputValue[]

export function all<T>(sql: string, ...params: Params): T[] {
  return getDb().prepare(sql).all(...params) as T[]
}

export function get<T>(sql: string, ...params: Params): T | undefined {
  return getDb().prepare(sql).get(...params) as T | undefined
}

export function run(sql: string, ...params: Params): void {
  getDb().prepare(sql).run(...params)
}

/** Run fn inside one transaction; nested calls join the outer one. */
export function tx<T>(fn: () => T): T {
  const d = getDb()
  if (txDepth > 0) return fn()
  d.exec('BEGIN')
  txDepth++
  try {
    const result = fn()
    d.exec('COMMIT')
    return result
  } catch (err) {
    d.exec('ROLLBACK')
    throw err
  } finally {
    txDepth--
  }
}

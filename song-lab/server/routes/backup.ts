import { z } from 'zod'
import { HANDLED, type Router } from '../http'
import * as store from '../store'

const id = z.string().min(1).max(100)
const nullableId = id.nullable()
const iso = z.string().min(1).max(40)
const text = z.string().max(100_000)
const status = z.enum(['draft', 'verified'])
const seconds = z.number().min(0).max(86_400).nullable()

// A backup is checked field by field before anything in the app is replaced.
const backupSchema = z.object({
  app: z.literal('sudanese-arabic-song-lab'),
  version: z.literal(1),
  exportedAt: iso,
  settings: z.record(z.string(), z.unknown()).default({}),
  songs: z.array(
    z.object({ id, title: text, artist: text, youtubeId: z.string().regex(/^[\w-]{11}$/), savedRate: z.number().min(0.1).max(4), notes: text, createdAt: iso }),
  ),
  lines: z.array(
    z.object({
      id, songId: id, order: z.number().int().min(0), arabic: text, translit: text, gloss: text,
      startSec: seconds, endSec: seconds, status, source: z.enum(['user', 'claude', 'tutor']), note: text,
      unsure: z.boolean().default(false),
    }),
  ),
  words: z.array(
    z.object({
      id, lineId: id, order: z.number().int().min(0), arabic: text, translit: text, gloss: text, root: text,
      register: z.enum(['everyday', 'poetic', 'unknown']), status, note: text, unsure: z.boolean().default(false),
      glossPart: text.default(''),
    }),
  ),
  cards: z.array(
    z.object({
      id, kind: z.enum(['word', 'line', 'phrase']), wordId: nullableId, lineId: nullableId, arabic: text, translit: text,
      gloss: text, direction: z.enum(['ar-en', 'en-ar']), due: iso, interval: z.number().min(0), ease: z.number().min(1),
      reps: z.number().int().min(0), lapses: z.number().int().min(0), suspended: z.boolean(), createdAt: iso,
    }),
  ),
  reviews: z.array(z.object({ id, cardId: id, reviewedAt: iso, grade: z.enum(['again', 'hard', 'good', 'easy']) })),
  questions: z.array(z.object({ id, lineId: nullableId, wordId: nullableId, text, resolved: z.boolean(), createdAt: iso })),
  recordings: z.array(
    z.object({ id, lineId: id, filePath: z.string().max(300), createdAt: iso, dataBase64: z.string().optional() }),
  ),
})

export function registerBackupRoutes(router: Router): void {
  router.get('/api/backup/export', (ctx) => {
    const withAudio = ctx.query.get('audio') !== '0'
    const body = JSON.stringify(store.exportBackup(withAudio), null, 1)
    const day = new Date().toISOString().slice(0, 10)
    ctx.res.writeHead(200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Disposition': `attachment; filename="songlab-backup-${day}.json"`,
      'Content-Length': Buffer.byteLength(body),
      'Cache-Control': 'no-store',
    })
    ctx.res.end(body)
    return HANDLED
  })

  router.post('/api/backup/import', async (ctx) => {
    const backup = await ctx.json(backupSchema)
    return store.importBackup(backup as store.Backup)
  })
}

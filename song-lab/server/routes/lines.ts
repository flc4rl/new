import { z } from 'zod'
import { HttpError, type Router } from '../http'
import * as store from '../store'
import { splitLines } from '../../shared/text'

const text = (max: number) => z.string().trim().max(max)
const seconds = z.number().min(0).max(86_400).nullable()

const newLines = z.object({
  /** Lyrics typed or pasted by the user, one line per row. */
  text: z.string().max(200_000).optional(),
  /** Add one empty line for the user to type into. */
  blank: z.boolean().optional(),
  /** Insert after this line; null for the top; leave out for the end. */
  afterLineId: z.string().nullable().optional(),
})

const linePatch = z.object({
  arabic: text(2000).optional(),
  translit: text(2000).optional(),
  gloss: text(2000).optional(),
  startSec: seconds.optional(),
  endSec: seconds.optional(),
  status: z.enum(['draft', 'verified']).optional(),
  source: z.enum(['user', 'claude', 'google', 'tutor']).optional(),
  note: text(5000).optional(),
})

export function registerLineRoutes(router: Router): void {
  router.post('/api/songs/:id/lines', async (ctx) => {
    const body = await ctx.json(newLines)
    const rows = body.blank ? [''] : splitLines(body.text ?? '')
    if (rows.length === 0) throw new HttpError(400, 'There are no lyrics to add. Type or paste one line per row.')
    return store.addLines(ctx.params.id, rows, body.afterLineId)
  })

  router.post('/api/songs/:id/clear-timings', (ctx) => {
    store.clearTimings(ctx.params.id)
  })

  router.patch('/api/lines/:id', async (ctx) => store.updateLine(ctx.params.id, await ctx.json(linePatch)))

  router.delete('/api/lines/:id', (ctx) => {
    store.deleteLine(ctx.params.id)
  })

  router.post('/api/lines/:id/move', async (ctx) => {
    const { delta } = await ctx.json(z.object({ delta: z.union([z.literal(-1), z.literal(1)]) }))
    return store.moveLine(ctx.params.id, delta)
  })
}

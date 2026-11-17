import { z } from 'zod'
import type { Router } from '../http'
import * as store from '../store'

const text = (max: number) => z.string().trim().max(max)
const direction = z.enum(['ar-en', 'en-ar'])

const wordPatch = z.object({
  arabic: text(300).min(1).optional(),
  translit: text(300).optional(),
  gloss: text(500).optional(),
  root: text(60).optional(),
  register: z.enum(['everyday', 'poetic', 'unknown']).optional(),
  status: z.enum(['draft', 'verified']).optional(),
  note: text(2000).optional(),
  glossPart: text(500).optional(),
})

const newCard = z.object({
  card: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('word'), wordId: z.string() }),
    z.object({ kind: z.literal('line'), lineId: z.string() }),
    z.object({
      kind: z.literal('phrase'),
      arabic: text(1000).min(1, 'Type the Arabic.'),
      translit: text(1000).default(''),
      gloss: text(1000).min(1, 'Type the meaning.'),
    }),
  ]),
  directions: z.array(direction).max(2).optional(),
})

const cardPatch = z.object({
  suspended: z.boolean().optional(),
  arabic: text(1000).min(1).optional(),
  translit: text(1000).optional(),
  gloss: text(1000).min(1).optional(),
})

const review = z.object({
  cardId: z.string(),
  grade: z.enum(['again', 'hard', 'good', 'easy']),
})

export function registerDeckRoutes(router: Router): void {
  // ---- words ----
  router.post('/api/lines/:id/split', (ctx) => store.splitLine(ctx.params.id))

  router.post('/api/lines/:id/words', async (ctx) => {
    const { arabic } = await ctx.json(z.object({ arabic: text(300).min(1, 'Type the word.') }))
    return store.addWord(ctx.params.id, arabic)
  })

  router.patch('/api/words/:id', async (ctx) => store.updateWord(ctx.params.id, await ctx.json(wordPatch)))

  router.delete('/api/words/:id', (ctx) => {
    store.deleteWord(ctx.params.id)
  })

  // ---- cards ----
  router.get('/api/cards', () => store.listCards())

  router.post('/api/cards', async (ctx) => {
    const body = await ctx.json(newCard)
    return store.createCards(body.card, body.directions)
  })

  router.patch('/api/cards/:id', async (ctx) => store.updateCard(ctx.params.id, await ctx.json(cardPatch)))

  router.delete('/api/cards/:id', (ctx) => {
    store.deleteCard(ctx.params.id)
  })

  // ---- review ----
  router.get('/api/review/queue', () => store.reviewQueue())

  router.post('/api/review', async (ctx) => {
    const body = await ctx.json(review)
    return store.gradeCard(body.cardId, body.grade)
  })
}

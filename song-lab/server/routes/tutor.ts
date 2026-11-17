import { z } from 'zod'
import { HANDLED, type Router } from '../http'
import { draftLines } from '../drafts'
import * as store from '../store'
import { tutorMarkdown } from '../tutorExport'

const text = (max: number) => z.string().trim().max(max)

const newQuestion = z
  .object({
    lineId: z.string().optional(),
    wordId: z.string().optional(),
    text: text(5000).min(1, 'Type the question.'),
  })
  .refine((q) => q.lineId || q.wordId, 'A question belongs to a line or a word.')

const questionPatch = z.object({
  text: text(5000).min(1).optional(),
  resolved: z.boolean().optional(),
})

const draftRequest = z.object({
  songId: z.string(),
  lineIds: z.array(z.string()).min(1, 'Choose at least one line to draft.').max(500),
})

export function registerTutorRoutes(router: Router): void {
  router.post('/api/draft', async (ctx) => {
    const body = await ctx.json(draftRequest)
    return draftLines(body.songId, body.lineIds)
  })

  router.post('/api/questions', async (ctx) => store.addQuestion(await ctx.json(newQuestion)))
  router.patch('/api/questions/:id', async (ctx) => store.updateQuestion(ctx.params.id, await ctx.json(questionPatch)))
  router.delete('/api/questions/:id', (ctx) => {
    store.deleteQuestion(ctx.params.id)
  })

  router.get('/api/tutor', () => store.tutorQueue())

  router.get('/api/tutor/export.md', (ctx) => {
    const groups = store.tutorQueue()
    const numbers = new Map<string, number>()
    for (const group of groups) {
      store.linesOfSong(group.song.id).forEach((line, i) => numbers.set(line.id, i + 1))
    }
    const body = tutorMarkdown(groups, numbers)
    const day = new Date().toISOString().slice(0, 10)
    ctx.res.writeHead(200, {
      'Content-Type': 'text/markdown; charset=utf-8',
      'Content-Disposition': `attachment; filename="tutor-queue-${day}.md"`,
      'Cache-Control': 'no-store',
    })
    ctx.res.end(body)
    return HANDLED
  })
}

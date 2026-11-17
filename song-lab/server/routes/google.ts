import { z } from 'zod'
import { HttpError, type Router } from '../http'
import { googleLookup } from '../google'
import { googleTranslateLines } from '../googleDrafts'
import * as store from '../store'

const translateRequest = z.object({
  songId: z.string(),
  lineIds: z.array(z.string()).min(1, 'Choose at least one line to translate.').max(500),
})

export function registerGoogleRoutes(router: Router): void {
  router.post('/api/google/translate', async (ctx) => {
    const body = await ctx.json(translateRequest)
    return googleTranslateLines(body.songId, body.lineIds)
  })

  // Dictionary entries for one word, for the word editor.
  router.get('/api/google/lookup', async (ctx) => {
    if (!store.getSettings().googleTranslate) throw new HttpError(503, 'Google Translate is switched off in Settings.')
    const text = (ctx.query.get('text') ?? '').trim()
    if (!text || text.length > 500) throw new HttpError(400, 'Give a word or a short line to look up.')
    return googleLookup(text)
  })
}

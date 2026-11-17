import { z } from 'zod'
import { HttpError, type Router } from '../http'
import { CLAUDE_MODEL, loadTranslitConfig } from '../config'
import { draftingAvailable } from '../drafts'
import * as store from '../store'
import { parseYouTubeId, watchUrl } from '../../shared/youtube'
import type { Meta } from '../../shared/types'

const text = (max: number) => z.string().trim().max(max)

const newSong = z.object({
  url: z.string().trim().min(1, 'Paste a YouTube link.'),
  title: text(300).min(1, 'Give the song a title.'),
  artist: text(300).default(''),
  notes: text(5000).default(''),
})

const songPatch = z.object({
  title: text(300).min(1).optional(),
  artist: text(300).optional(),
  notes: text(5000).optional(),
  savedRate: z.number().min(0.1).max(4).optional(),
  url: z.string().trim().optional(),
})

const settingsPatch = z.object({
  newPerDay: z.number().int().min(0).max(500).optional(),
  cardDirections: z.enum(['both', 'ar-en', 'en-ar']).optional(),
  tapOffsetSec: z.number().min(0).max(1).optional(),
  loopLeadInSec: z.number().min(0).max(2).optional(),
  loopRepeats: z.number().int().min(0).max(99).optional(),
  loopPauseSec: z.number().min(0).max(30).optional(),
  autoDraft: z.boolean().optional(),
  googleTranslate: z.boolean().optional(),
})

function requireYouTubeId(url: string): string {
  const id = parseYouTubeId(url)
  if (!id) throw new HttpError(400, 'That does not look like a YouTube link. Paste the address of the video page.')
  return id
}

export function registerSongRoutes(router: Router): void {
  router.get('/api/meta', (): Meta => ({
    claude: draftingAvailable(),
    model: CLAUDE_MODEL,
    translit: loadTranslitConfig(),
  }))

  router.get('/api/settings', () => store.getSettings())
  router.put('/api/settings', async (ctx) => store.saveSettings(await ctx.json(settingsPatch)))

  router.get('/api/songs', () => store.listSongs())

  router.post('/api/songs', async (ctx) => {
    const body = await ctx.json(newSong)
    return store.createSong({
      title: body.title,
      artist: body.artist,
      notes: body.notes,
      youtubeId: requireYouTubeId(body.url),
    })
  })

  router.get('/api/songs/:id', (ctx) => store.getSongDetail(ctx.params.id))

  router.patch('/api/songs/:id', async (ctx) => {
    const { url, ...rest } = await ctx.json(songPatch)
    return store.updateSong(ctx.params.id, url ? { ...rest, youtubeId: requireYouTubeId(url) } : rest)
  })

  router.delete('/api/songs/:id', (ctx) => {
    store.deleteSong(ctx.params.id, ctx.query.get('keepCards') === '1')
  })

  // Ask YouTube for the public title of a video, to pre-fill the form.
  // This reads metadata only; no audio or video is fetched.
  router.get('/api/youtube/lookup', async (ctx) => {
    const youtubeId = parseYouTubeId(ctx.query.get('url') ?? '')
    if (!youtubeId) return { youtubeId: null, title: null, author: null, available: null }
    try {
      const res = await fetch(
        `https://www.youtube.com/oembed?format=json&url=${encodeURIComponent(watchUrl(youtubeId))}`,
        { signal: AbortSignal.timeout(5000) },
      )
      if (res.ok) {
        const data = (await res.json()) as { title?: string; author_name?: string }
        return { youtubeId, title: data.title ?? null, author: data.author_name ?? null, available: true }
      }
      // YouTube answers 401 or 403 for private videos and for videos that do not allow embedding.
      const refused = res.status === 401 || res.status === 403 || res.status === 404
      return { youtubeId, title: null, author: null, available: refused ? false : null }
    } catch {
      return { youtubeId, title: null, author: null, available: null }
    }
  })
}

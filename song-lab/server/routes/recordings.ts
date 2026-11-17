import fs from 'node:fs'
import { HANDLED, type Router } from '../http'
import * as store from '../store'

/** A minute of compressed speech is well under a megabyte; this leaves plenty of room. */
const MAX_UPLOAD = 25 * 1024 * 1024

export function registerRecordingRoutes(router: Router): void {
  // Save the take for a line, replacing the one before.
  router.put('/api/lines/:id/recording', async (ctx) => {
    const extension = store.audioExtension(ctx.req.headers['content-type'] ?? '')
    const data = await ctx.raw(MAX_UPLOAD)
    return store.saveRecording(ctx.params.id, data, extension)
  })

  // Stream the take, with byte ranges so every browser can seek in it.
  router.get('/api/lines/:id/recording', (ctx) => {
    const { fullPath, contentType } = store.recordingFile(ctx.params.id)
    const size = fs.statSync(fullPath).size
    const headers = { 'Content-Type': contentType, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache' }
    const range = /^bytes=(\d*)-(\d*)$/.exec(ctx.req.headers.range ?? '')
    if (range && (range[1] || range[2])) {
      let start = range[1] ? Number(range[1]) : size - Number(range[2])
      let end = range[1] && range[2] ? Number(range[2]) : size - 1
      start = Math.max(0, start)
      end = Math.min(size - 1, end)
      if (start > end) {
        ctx.res.writeHead(416, { 'Content-Range': `bytes */${size}` }).end()
        return HANDLED
      }
      ctx.res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': end - start + 1 })
      fs.createReadStream(fullPath, { start, end }).pipe(ctx.res)
      return HANDLED
    }
    ctx.res.writeHead(200, { ...headers, 'Content-Length': size })
    fs.createReadStream(fullPath).pipe(ctx.res)
    return HANDLED
  })

  router.delete('/api/lines/:id/recording', (ctx) => {
    store.getLine(ctx.params.id)
    store.deleteRecording(ctx.params.id)
  })
}

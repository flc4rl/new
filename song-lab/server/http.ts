import type { IncomingMessage, ServerResponse } from 'node:http'
import { ZodError, type ZodType } from 'zod'

/** An error the client should see, with the HTTP status to send. */
export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export interface Ctx {
  req: IncomingMessage
  res: ServerResponse
  params: Record<string, string>
  query: URLSearchParams
  /** Parse the JSON body against a schema. */
  json<T>(schema: ZodType<T>): Promise<T>
  /** Read the raw body, for audio uploads. */
  raw(limitBytes: number): Promise<Buffer>
}

/** Returned by handlers that wrote the response themselves. */
export const HANDLED = Symbol('handled')

type Handler = (ctx: Ctx) => unknown | Promise<unknown>

interface Route {
  method: string
  pattern: RegExp
  keys: string[]
  handler: Handler
}

const JSON_LIMIT = 256 * 1024 * 1024

export async function readBody(req: IncomingMessage, limitBytes: number): Promise<Buffer> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    size += (chunk as Buffer).length
    if (size > limitBytes) throw new HttpError(413, 'That upload is too large.')
    chunks.push(chunk as Buffer)
  }
  return Buffer.concat(chunks)
}

function describeZodError(err: ZodError): string {
  const issue = err.issues[0]
  const where = issue.path.length ? `${issue.path.join('.')}: ` : ''
  return `Invalid request. ${where}${issue.message}`
}

export class Router {
  private routes: Route[] = []

  private add(method: string, path: string, handler: Handler): void {
    const keys: string[] = []
    const source = path.replace(/:([A-Za-z]+)/g, (_, key: string) => {
      keys.push(key)
      return '([^/]+)'
    })
    this.routes.push({ method, pattern: new RegExp(`^${source}$`), keys, handler })
  }

  get(path: string, handler: Handler): void {
    this.add('GET', path, handler)
  }
  post(path: string, handler: Handler): void {
    this.add('POST', path, handler)
  }
  put(path: string, handler: Handler): void {
    this.add('PUT', path, handler)
  }
  patch(path: string, handler: Handler): void {
    this.add('PATCH', path, handler)
  }
  delete(path: string, handler: Handler): void {
    this.add('DELETE', path, handler)
  }

  /** Handle an /api request. Always writes a response. */
  async handle(req: IncomingMessage, res: ServerResponse, url: URL): Promise<void> {
    try {
      let pathMatched = false
      for (const route of this.routes) {
        const match = route.pattern.exec(url.pathname)
        if (!match) continue
        pathMatched = true
        if (route.method !== req.method) continue
        const params: Record<string, string> = {}
        route.keys.forEach((key, i) => (params[key] = decodeURIComponent(match[i + 1])))
        const ctx: Ctx = {
          req,
          res,
          params,
          query: url.searchParams,
          async json(schema) {
            const type = req.headers['content-type'] ?? ''
            if (!type.includes('application/json')) throw new HttpError(415, 'Expected a JSON body.')
            const text = (await readBody(req, JSON_LIMIT)).toString('utf8')
            let data: unknown
            try {
              data = JSON.parse(text)
            } catch {
              throw new HttpError(400, 'The request body is not valid JSON.')
            }
            return schema.parse(data)
          },
          raw: (limitBytes) => readBody(req, limitBytes),
        }
        const result = await route.handler(ctx)
        if (result === HANDLED) return
        if (result === undefined) {
          res.writeHead(204).end()
          return
        }
        sendJson(res, 200, result)
        return
      }
      throw new HttpError(pathMatched ? 405 : 404, pathMatched ? 'Method not allowed.' : 'Not found.')
    } catch (err) {
      if (res.headersSent) {
        res.end()
        return
      }
      if (err instanceof HttpError) {
        sendJson(res, err.status, { error: err.message })
      } else if (err instanceof ZodError) {
        sendJson(res, 400, { error: describeZodError(err) })
      } else {
        console.error(err)
        sendJson(res, 500, { error: 'Something went wrong on the server. The terminal has the details.' })
      }
    }
  }
}

export function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(text),
    'Cache-Control': 'no-store',
  })
  res.end(text)
}

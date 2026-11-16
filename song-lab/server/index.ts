import http, { type IncomingMessage, type ServerResponse } from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { PORT, ROOT, claudeAvailable } from './config'
import { DATA_DIR, getDb } from './db'
import { sendJson } from './http'
import { buildRouter } from './app'

const DEV = process.argv.includes('--dev')
const OPEN = process.argv.includes('--open')
const DIST = path.join(ROOT, 'dist')

const router = buildRouter()

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

/** Only answer requests addressed to this machine, so another site cannot reach the app by a DNS trick. */
function hostAllowed(req: IncomingMessage): boolean {
  const host = (req.headers.host ?? '').replace(/:\d+$/, '')
  return LOCAL_HOSTS.has(host)
}

/** The API is for the app's own pages only. */
function originAllowed(req: IncomingMessage): boolean {
  if (req.headers['sec-fetch-site'] === 'cross-site') return false
  const origin = req.headers.origin
  if (!origin) return true
  try {
    return new URL(origin).host === req.headers.host
  } catch {
    return false
  }
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.map': 'application/json; charset=utf-8',
}

function serveStatic(res: ServerResponse, pathname: string): void {
  const index = path.join(DIST, 'index.html')
  if (!fs.existsSync(index)) {
    res.writeHead(503, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('The app has not been built yet. Stop the server and run "npm start".')
    return
  }
  let file = path.join(DIST, decodeURIComponent(pathname))
  const inside = file === DIST || file.startsWith(DIST + path.sep)
  if (!inside || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = index
  const type = MIME[path.extname(file)] ?? 'application/octet-stream'
  const hashed = file.startsWith(path.join(DIST, 'assets') + path.sep)
  res.writeHead(200, {
    'Content-Type': type,
    'Cache-Control': hashed ? 'public, max-age=31536000, immutable' : 'no-cache',
  })
  fs.createReadStream(file).pipe(res)
}

function openBrowser(url: string): void {
  const [command, args] =
    process.platform === 'darwin'
      ? ['open', [url]]
      : process.platform === 'win32'
        ? ['cmd', ['/c', 'start', '', url]]
        : ['xdg-open', [url]]
  try {
    const child = spawn(command as string, args as string[], { stdio: 'ignore', detached: true })
    child.on('error', () => {})
    child.unref()
  } catch {
    // No browser to open; the address is printed below.
  }
}

async function main(): Promise<void> {
  getDb()

  // In development Vite serves the frontend from source, inside this same process.
  const vite = DEV
    ? await (await import('vite')).createServer({
        root: ROOT,
        server: { middlewareMode: true },
        appType: 'spa',
      })
    : null

  const server = http.createServer((req, res) => {
    if (!hostAllowed(req)) {
      sendJson(res, 403, { error: 'This app only answers on localhost.' })
      return
    }
    const url = new URL(req.url ?? '/', `http://${req.headers.host}`)
    if (url.pathname.startsWith('/api/')) {
      if (!originAllowed(req)) {
        sendJson(res, 403, { error: 'Requests from other sites are not accepted.' })
        return
      }
      void router.handle(req, res, url)
      return
    }
    if (vite) {
      vite.middlewares(req, res, () => {
        res.writeHead(404).end()
      })
      return
    }
    serveStatic(res, url.pathname)
  })

  server.on('error', (err: NodeJS.ErrnoException) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`\nPort ${PORT} is already in use. Song Lab may already be running: try http://localhost:${PORT}`)
      console.error(`To use another port, start with PORT=5180 npm start\n`)
      process.exit(1)
    }
    throw err
  })

  server.listen(PORT, '127.0.0.1', () => {
    const url = `http://localhost:${PORT}`
    console.log(`\n  Sudanese Arabic Song Lab is running at ${url}`)
    console.log(`  Data folder: ${DATA_DIR}`)
    console.log(
      claudeAvailable()
        ? '  Claude drafts: on'
        : '  Claude drafts: off (set ANTHROPIC_API_KEY to turn them on; everything else works without it)',
    )
    console.log('  Press Ctrl+C to stop.\n')
    if (OPEN) openBrowser(url)
  })
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})

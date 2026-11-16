import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import type { AddressInfo } from 'node:net'

// Point the app at a throwaway data folder before any server module loads.
export const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'songlab-test-'))
process.env.SONGLAB_DATA_DIR = dataDir
delete process.env.ANTHROPIC_API_KEY
delete process.env.ANTHROPIC_AUTH_TOKEN

export interface TestApp {
  url: string
  call: <T = any>(method: string, path: string, body?: unknown) => Promise<{ status: number; data: T }>
  close: () => Promise<void>
}

/** Start the API on a free port, backed by the throwaway data folder. */
export async function startApp(): Promise<TestApp> {
  const { buildRouter } = await import('../server/app')
  const { closeDb } = await import('../server/db')
  const router = buildRouter()
  const server = http.createServer((req, res) => {
    void router.handle(req, res, new URL(req.url ?? '/', 'http://localhost'))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  return {
    url,
    async call(method, p, body) {
      const res = await fetch(url + p, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const text = await res.text()
      return { status: res.status, data: text ? JSON.parse(text) : null }
    },
    async close() {
      await new Promise((resolve) => server.close(resolve))
      closeDb()
      fs.rmSync(dataDir, { recursive: true, force: true })
    },
  }
}

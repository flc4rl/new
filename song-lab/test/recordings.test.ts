import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { dataDir, startApp, type TestApp } from './helpers'

let app: TestApp
let songId: string
let lineId: string

before(async () => {
  app = await startApp()
  songId = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Takes' })).data.id
  lineId = (await app.call('POST', `/api/songs/${songId}/lines`, { text: 'الشمس طلعت\nالقمر غاب' })).data[0].id
})
after(() => app.close())

const put = (id: string, body: Buffer, type: string) =>
  fetch(`${app.url}/api/lines/${id}/recording`, { method: 'PUT', headers: { 'Content-Type': type }, body })

const files = () => fs.readdirSync(path.join(dataDir, 'recordings'))

test('a take is saved as a file in the data folder and played back', async () => {
  const take = Buffer.from('first take, pretend this is audio')
  const res = await put(lineId, take, 'audio/webm;codecs=opus')
  assert.equal(res.status, 200)
  const rec = (await res.json()) as { filePath: string }
  assert.equal(rec.filePath, `recordings/${lineId}.webm`)
  assert.deepEqual(files(), [`${lineId}.webm`])

  const back = await fetch(`${app.url}/api/lines/${lineId}/recording`)
  assert.equal(back.headers.get('content-type'), 'audio/webm')
  assert.deepEqual(Buffer.from(await back.arrayBuffer()), take)

  const detail = (await app.call('GET', `/api/songs/${songId}`)).data
  assert.equal(detail.recordings.length, 1)
})

test('byte ranges are served, so the take can be seeked', async () => {
  const res = await fetch(`${app.url}/api/lines/${lineId}/recording`, { headers: { Range: 'bytes=6-9' } })
  assert.equal(res.status, 206)
  assert.equal(res.headers.get('content-range'), 'bytes 6-9/33')
  assert.equal(await res.text(), 'take')
})

test('only the latest take per line is kept', async () => {
  const res = await put(lineId, Buffer.from('second take'), 'audio/mp4')
  assert.equal(res.status, 200)
  assert.deepEqual(files(), [`${lineId}.m4a`], 'the earlier file is gone')
  const back = await fetch(`${app.url}/api/lines/${lineId}/recording`)
  assert.equal(back.headers.get('content-type'), 'audio/mp4')
  assert.equal(await back.text(), 'second take')
  assert.equal((await app.call('GET', `/api/songs/${songId}`)).data.recordings.length, 1)
})

test('uploads that are not audio, or empty, are refused', async () => {
  assert.equal((await put(lineId, Buffer.from('<html>'), 'text/html')).status, 415)
  assert.equal((await put(lineId, Buffer.alloc(0), 'audio/webm')).status, 400)
  assert.equal((await put('no-such-line', Buffer.from('x'), 'audio/webm')).status, 404)
})

test('a take can be deleted, and goes with its line', async () => {
  await app.call('DELETE', `/api/lines/${lineId}/recording`)
  assert.deepEqual(files(), [])
  assert.equal((await fetch(`${app.url}/api/lines/${lineId}/recording`)).status, 404)

  await put(lineId, Buffer.from('again'), 'audio/webm')
  await app.call('DELETE', `/api/lines/${lineId}`)
  assert.deepEqual(files(), [], 'deleting the line deletes its take')
})

test('deleting a song deletes its takes', async () => {
  const lines = (await app.call('GET', `/api/songs/${songId}`)).data.lines
  await put(lines[0].id, Buffer.from('take'), 'audio/ogg')
  assert.equal(files().length, 1)
  await app.call('DELETE', `/api/songs/${songId}?keepCards=0`)
  assert.deepEqual(files(), [])
})

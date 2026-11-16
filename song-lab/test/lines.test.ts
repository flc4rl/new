import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startApp, type TestApp } from './helpers'

let app: TestApp
let songId: string

// Plain practice sentences written for these tests, not song lyrics.
const PASTED = 'الشمس طلعت\n\nالقمر غاب\r\nالنيل طويل\n'

before(async () => {
  app = await startApp()
  songId = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Lines' })).data.id
})
after(() => app.close())

const lines = async () => (await app.call('GET', `/api/songs/${songId}`)).data.lines

test('pasted lyrics become one line per row', async () => {
  const res = await app.call('POST', `/api/songs/${songId}/lines`, { text: PASTED })
  assert.equal(res.status, 200)
  assert.deepEqual(res.data.map((l: any) => l.arabic), ['الشمس طلعت', 'القمر غاب', 'النيل طويل'])
  assert.deepEqual(res.data.map((l: any) => l.order), [0, 1, 2])
  assert.ok(res.data.every((l: any) => l.status === 'draft' && l.source === 'user' && l.translit === '' && l.startSec === null))
})

test('empty paste is refused', async () => {
  const res = await app.call('POST', `/api/songs/${songId}/lines`, { text: '  \n \n' })
  assert.equal(res.status, 400)
})

test('transliteration and gloss typed by hand are saved', async () => {
  const [first] = await lines()
  const res = await app.call('PATCH', `/api/lines/${first.id}`, { translit: 'ash-shamis Tala3at', gloss: 'The sun came up' })
  assert.equal(res.data.translit, 'ash-shamis Tala3at')
  const [again] = await lines()
  assert.equal(again.translit, 'ash-shamis Tala3at')
  assert.equal(again.gloss, 'The sun came up')
})

test('a verified line goes back to draft only when its Arabic changes', async () => {
  const [first] = await lines()
  await app.call('PATCH', `/api/lines/${first.id}`, { status: 'verified' })
  let res = await app.call('PATCH', `/api/lines/${first.id}`, { translit: 'ash-shamis Tal3at', gloss: 'The sun rose' })
  assert.equal(res.data.status, 'verified', 'editing transliteration or gloss keeps it verified')
  res = await app.call('PATCH', `/api/lines/${first.id}`, { arabic: first.arabic })
  assert.equal(res.data.status, 'verified', 'saving the same Arabic keeps it verified')
  res = await app.call('PATCH', `/api/lines/${first.id}`, { arabic: 'الشمس طلعت بدري' })
  assert.equal(res.data.status, 'draft', 'changing the Arabic makes it a draft again')
})

test('lines can be inserted, moved and deleted, and stay numbered in order', async () => {
  const before = await lines()
  const inserted = await app.call('POST', `/api/songs/${songId}/lines`, { blank: true, afterLineId: before[0].id })
  assert.equal(inserted.data.length, 4)
  assert.equal(inserted.data[1].arabic, '')
  assert.deepEqual(inserted.data.map((l: any) => l.order), [0, 1, 2, 3])

  const moved = await app.call('POST', `/api/lines/${inserted.data[1].id}/move`, { delta: 1 })
  assert.equal(moved.data[2].id, inserted.data[1].id)

  await app.call('DELETE', `/api/lines/${inserted.data[1].id}`)
  const final = await lines()
  assert.deepEqual(final.map((l: any) => l.id), before.map((l: any) => l.id))
  assert.deepEqual(final.map((l: any) => l.order), [0, 1, 2])
})

test('invalid edits are refused', async () => {
  const [first] = await lines()
  assert.equal((await app.call('PATCH', `/api/lines/${first.id}`, { status: 'done' })).status, 400)
  assert.equal((await app.call('PATCH', `/api/lines/${first.id}`, { startSec: -1 })).status, 400)
  assert.equal((await app.call('PATCH', '/api/lines/nope', { gloss: 'x' })).status, 404)
})

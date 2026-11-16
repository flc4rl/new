import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startApp, type TestApp } from './helpers'

let app: TestApp
before(async () => {
  app = await startApp()
})
after(() => app.close())

test('a song is added from a YouTube link and listed with progress', async () => {
  const created = await app.call('POST', '/api/songs', {
    url: 'https://youtu.be/AAAAAAAAAAA',
    title: ' First song ',
    artist: 'Singer',
  })
  assert.equal(created.status, 200)
  assert.equal(created.data.youtubeId, 'AAAAAAAAAAA')
  assert.equal(created.data.title, 'First song')
  assert.equal(created.data.savedRate, 1)

  const list = await app.call('GET', '/api/songs')
  assert.equal(list.data.length, 1)
  assert.deepEqual(
    { lines: list.data[0].lineCount, synced: list.data[0].syncedCount, verified: list.data[0].verifiedCount, cards: list.data[0].cardCount },
    { lines: 0, synced: 0, verified: 0, cards: 0 },
  )
})

test('a link that is not YouTube is refused with a clear message', async () => {
  const res = await app.call('POST', '/api/songs', { url: 'https://example.com/song', title: 'Nope' })
  assert.equal(res.status, 400)
  assert.match(res.data.error, /YouTube link/)
})

test('the last speed is remembered per song', async () => {
  const [song] = (await app.call('GET', '/api/songs')).data
  await app.call('PATCH', `/api/songs/${song.id}`, { savedRate: 0.5 })
  const detail = await app.call('GET', `/api/songs/${song.id}`)
  assert.equal(detail.data.song.savedRate, 0.5)
})

test('settings start from the defaults and can be changed', async () => {
  const initial = await app.call('GET', '/api/settings')
  assert.equal(initial.data.newPerDay, 10)
  const saved = await app.call('PUT', '/api/settings', { newPerDay: 5 })
  assert.equal(saved.data.newPerDay, 5)
  assert.equal((await app.call('PUT', '/api/settings', { newPerDay: -1 })).status, 400)
  await app.call('PUT', '/api/settings', { newPerDay: 10 })
})

test('with no API key the app reports that Claude drafts are off', async () => {
  const meta = await app.call('GET', '/api/meta')
  assert.equal(meta.data.claude, false)
  assert.ok(meta.data.translit.letters.length > 0)
})

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { dataDir, startApp, type TestApp } from './helpers'

let app: TestApp

before(async () => {
  app = await startApp()
})
after(() => app.close())

async function buildUpData() {
  const song = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Backup song', artist: 'Singer', notes: 'From the lesson' })).data
  await app.call('PATCH', `/api/songs/${song.id}`, { savedRate: 0.75 })
  // Practice sentences written for these tests, not song lyrics.
  const lines = (await app.call('POST', `/api/songs/${song.id}/lines`, { text: 'الشمس طلعت\nالقمر غاب' })).data
  await app.call('PATCH', `/api/lines/${lines[0].id}`, { translit: 'ash-shamis Tal3at', gloss: 'The sun came out', startSec: 3.5, endSec: 6, status: 'verified' })
  await app.call('PATCH', `/api/lines/${lines[1].id}`, { startSec: 6 })
  const words = (await app.call('POST', `/api/lines/${lines[0].id}/split`)).data
  await app.call('PATCH', `/api/words/${words[0].id}`, { translit: 'ash-shamis', gloss: 'the sun', root: 'ش م س', register: 'everyday' })
  const cards = (await app.call('POST', '/api/cards', { card: { kind: 'word', wordId: words[0].id } })).data
  await app.call('POST', '/api/cards', { card: { kind: 'phrase', arabic: 'كيف الحال', translit: 'keef al-Haal', gloss: 'How are you?' } })
  await app.call('POST', '/api/review', { cardId: cards[0].id, grade: 'again' })
  await app.call('POST', '/api/review', { cardId: cards[0].id, grade: 'good' })
  await app.call('POST', '/api/questions', { wordId: words[1].id, text: 'Tala3at or Tal3at?' })
  await fetch(`${app.url}/api/lines/${lines[0].id}/recording`, { method: 'PUT', headers: { 'Content-Type': 'audio/webm' }, body: Buffer.from('a take') })
  await app.call('PUT', '/api/settings', { newPerDay: 15 })
  return song
}

async function exportText(audio = true) {
  const res = await fetch(`${app.url}/api/backup/export${audio ? '' : '?audio=0'}`)
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-disposition') ?? '', /songlab-backup-\d{4}-\d{2}-\d{2}\.json/)
  return res.text()
}

const withoutStamp = (text: string) => ({ ...JSON.parse(text), exportedAt: null })

test('export, wipe the data folder, import: everything returns', async () => {
  await buildUpData()
  const backup = await exportText()
  const parsed = JSON.parse(backup)
  assert.deepEqual(
    [parsed.songs.length, parsed.lines.length, parsed.words.length, parsed.cards.length, parsed.reviews.length, parsed.questions.length, parsed.recordings.length],
    [1, 2, 2, 4, 2, 1, 1],
  )
  assert.equal(Buffer.from(parsed.recordings[0].dataBase64, 'base64').toString(), 'a take')

  // Wipe the data folder while the app is running.
  fs.rmSync(dataDir, { recursive: true, force: true })
  assert.equal((await app.call('GET', '/api/songs')).data.length, 0, 'the app starts again from nothing')

  const res = await app.call('POST', '/api/backup/import', JSON.parse(backup))
  assert.equal(res.status, 200, JSON.stringify(res.data))
  assert.deepEqual(res.data, { songs: 1, lines: 2, words: 2, cards: 4, reviews: 2, questions: 1, recordings: 1 })

  assert.deepEqual(withoutStamp(await exportText()), withoutStamp(backup), 'the export after import matches the one before')
  const [song] = (await app.call('GET', '/api/songs')).data
  assert.deepEqual([song.lineCount, song.syncedCount, song.verifiedCount, song.cardCount], [2, 2, 1, 2])
  const lineId = parsed.recordings[0].lineId
  assert.equal(await (await fetch(`${app.url}/api/lines/${lineId}/recording`)).text(), 'a take')
  assert.ok(fs.existsSync(path.join(dataDir, 'recordings', `${lineId}.webm`)))
  assert.equal((await app.call('GET', '/api/settings')).data.newPerDay, 15)
})

test('an export without recordings is smaller, and importing it keeps takes still on disk', async () => {
  const text = await exportText(false)
  assert.ok(!text.includes('dataBase64'))
  let res = await app.call('POST', '/api/backup/import', JSON.parse(text))
  assert.deepEqual([res.data.recordings, res.data.reviews], [1, 2], 'the take on disk is kept')

  fs.rmSync(path.join(dataDir, 'recordings'), { recursive: true, force: true })
  res = await app.call('POST', '/api/backup/import', JSON.parse(text))
  assert.equal(res.data.recordings, 0, 'a take with no audio and no file is dropped')
  assert.equal((await app.call('GET', '/api/songs')).data.length, 1)
})

test('a file that is not a Song Lab backup is refused and nothing is changed', async () => {
  const before = await exportText()
  for (const bad of [{ hello: 'world' }, { ...JSON.parse(before), version: 2 }, { ...JSON.parse(before), songs: [{ id: 'x' }] }]) {
    const res = await app.call('POST', '/api/backup/import', bad)
    assert.equal(res.status, 400)
  }
  const res = await fetch(`${app.url}/api/backup/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{not json' })
  assert.equal(res.status, 400)
  assert.deepEqual(withoutStamp(await exportText()), withoutStamp(before))
})

import assert from 'node:assert/strict'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { after, before, beforeEach, test } from 'node:test'
import { startApp, type TestApp } from './helpers'

// A stand-in for Google Translate, answering in the format of the website's own requests.
// Practice sentences written for these tests, not song lyrics.
const DICTIONARY: Record<string, { trans: string; dict?: { pos: string; terms: string[] }[] }> = {
  'الشمس طلعت': { trans: 'The sun came out' },
  'القمر غاب': { trans: 'The moon disappeared' },
  'الشمس': { trans: 'the sun', dict: [{ pos: 'noun', terms: ['sun', 'sunshine'] }] },
  'طلعت': { trans: 'came out', dict: [{ pos: 'verb', terms: ['rose', 'came out', 'went up'] }] },
  'القمر': { trans: 'the moon', dict: [{ pos: 'noun', terms: ['moon'] }] },
  'غاب': { trans: 'absent', dict: [{ pos: 'verb', terms: ['be absent', 'disappear', 'set'] }] },
}
let calls: string[] = []
let refuse = false
const google = http.createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://x')
  const q = url.searchParams.get('q') ?? ''
  calls.push(q)
  if (refuse) {
    res.writeHead(429, { 'content-type': 'text/html' })
    res.end('<html><title>Sorry...</title></html>')
    return
  }
  const hit = DICTIONARY[q] ?? { trans: q }
  res.writeHead(200, { 'content-type': 'application/json' })
  res.end(JSON.stringify({
    sentences: [{ trans: hit.trans, orig: q }, { src_translit: 'romanised' }],
    ...(hit.dict ? { dict: hit.dict.map((d) => ({ ...d, entry: d.terms.map((word) => ({ word })) })) } : {}),
    src: 'ar',
  }))
})

let app: TestApp
let songId: string
let lineIds: string[]

before(async () => {
  await new Promise<void>((r) => google.listen(0, '127.0.0.1', r))
  process.env.SONGLAB_GOOGLE_URL = `http://127.0.0.1:${(google.address() as AddressInfo).port}`
  app = await startApp()
  songId = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Google' })).data.id
  lineIds = (await app.call('POST', `/api/songs/${songId}/lines`, { text: 'الشمس طلعت\nالقمر غاب' })).data.map((l: any) => l.id)
})
beforeEach(async () => {
  calls = []
  refuse = false
  const { resetGoogleForTests } = await import('../server/google')
  resetGoogleForTests()
})
after(async () => {
  await app.close()
  google.close()
  delete process.env.SONGLAB_GOOGLE_URL
})

const detail = async () => (await app.call('GET', `/api/songs/${songId}`)).data

test('Google fills the English, the words, their meanings and the hover links, as drafts', async () => {
  await app.call('PATCH', `/api/lines/${lineIds[1]}`, { gloss: 'The moon has set' })
  const res = await app.call('POST', '/api/google/translate', { songId, lineIds })
  assert.equal(res.status, 200, JSON.stringify(res.data))
  assert.deepEqual([res.data.updatedLines, res.data.updatedWords], [2, 4])

  const d = await detail()
  assert.deepEqual([d.lines[0].gloss, d.lines[0].source, d.lines[0].status], ['The sun came out', 'google', 'draft'])
  assert.equal(d.lines[1].gloss, 'The moon has set', 'English typed by hand is kept')
  assert.equal(d.lines[1].source, 'user')
  assert.equal(d.lines[0].translit, '', 'Google does not write the transliteration')

  const words = Object.fromEntries(d.words.map((w: any) => [w.arabic, w]))
  assert.equal(words['الشمس'].gloss, 'sun, sunshine')
  assert.equal(words['طلعت'].gloss, 'rose, came out, went up')
  assert.equal(words['الشمس'].glossPart, 'sun', 'linked to the English')
  assert.equal(words['طلعت'].glossPart, 'came out')
  assert.equal(words['غاب'].glossPart, 'set', 'a dictionary sense that matches the hand-typed English is linked')
  assert.ok(d.words.every((w: any) => w.status === 'draft'))
})

test('each word and line is asked of Google only once', async () => {
  const d = await detail()
  for (const w of d.words) await app.call('PATCH', `/api/words/${w.id}`, { gloss: '', glossPart: '' })
  await app.call('POST', '/api/google/translate', { songId, lineIds })
  assert.deepEqual(calls, [], 'everything came from the cache')
})

test('verified lines and words are never touched', async () => {
  const d = await detail()
  const word = d.words[0]
  await app.call('PATCH', `/api/words/${word.id}`, { gloss: '', glossPart: '', status: 'verified' })
  await app.call('PATCH', `/api/lines/${lineIds[0]}`, { gloss: '', status: 'verified' })
  await app.call('POST', '/api/google/translate', { songId, lineIds: [lineIds[0]] })
  const after = await detail()
  assert.equal(after.lines[0].gloss, '')
  assert.equal(after.words.find((w: any) => w.id === word.id).gloss, '')
  await app.call('PATCH', `/api/lines/${lineIds[0]}`, { status: 'draft' })
})

test('the dictionary of a word can be looked up for the word editor', async () => {
  const res = await app.call('GET', `/api/google/lookup?text=${encodeURIComponent('غاب')}`)
  assert.deepEqual(res.data.dictionary, [{ pos: 'verb', terms: ['be absent', 'disappear', 'set'] }])
  assert.equal(res.data.romanization, 'romanised')
})

test('when Google refuses, the app says so, keeps what it has and pauses further requests', async () => {
  refuse = true
  const [newLine] = (await app.call('POST', `/api/songs/${songId}/lines`, { text: 'النيل طويل' })).data.slice(-1)
  const res = await app.call('POST', '/api/google/translate', { songId, lineIds: [newLine.id] })
  assert.equal(res.status, 503)
  assert.match(res.data.error, /Google Translate is not answering/)
  const count = calls.length
  const again = await app.call('GET', `/api/google/lookup?text=${encodeURIComponent('النيل')}`)
  assert.equal(again.status, 503)
  assert.equal(calls.length, count, 'no new request while paused')
  assert.equal((await detail()).lines[0].gloss, '', 'nothing else changed')
})

test('Google can be switched off', async () => {
  await app.call('PUT', '/api/settings', { googleTranslate: false })
  assert.equal((await app.call('POST', '/api/google/translate', { songId, lineIds })).status, 503)
  assert.equal((await app.call('GET', '/api/google/lookup?text=x')).status, 503)
  await app.call('PUT', '/api/settings', { googleTranslate: true })
})

test('links allow common English endings', async () => {
  const { findInGloss } = await import('../server/googleDrafts')
  assert.equal(findInGloss('The moon disappeared, my darling', ['disappear']), 'disappeared')
  assert.equal(findInGloss('Red roses for you', ['rose']), 'roses')
  assert.equal(findInGloss('She is coming', ['come', 'coming']), 'coming')
  assert.equal(findInGloss('Once more', ['on']), '', 'never part of another word')
  assert.equal(findInGloss('a (strange) day', ['strange']), 'strange')
})

import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startApp, type TestApp } from './helpers'
import type { DraftLine, DraftRequestLine } from '../server/claude'

let app: TestApp
let songId: string
let lineIds: string[]
let requests: DraftRequestLine[][] = []

// Practice sentences written for these tests, not song lyrics.
const DRAFTS: Record<string, Omit<DraftLine, 'key'>> = {
  'الشمس طلعت': {
    translit: 'ash-shamis Tal3at', gloss: 'The sun came out', unsure: false, note: '',
    words: [
      { arabic: 'الشمس', translit: 'ash-shamis', gloss: 'the sun', root: 'ش م س', register: 'everyday', unsure: false, note: '' },
      { arabic: 'طلعت', translit: 'Tal3at', gloss: 'came out', root: 'ط ل ع', register: 'everyday', unsure: false, note: '' },
    ],
  },
  'القمر غاب': {
    translit: 'al-gamar gaab', gloss: 'The moon set', unsure: true, note: 'gaab: the ghayn is softened here',
    words: [
      { arabic: 'القمر', translit: 'al-gamar', gloss: 'the moon', root: '', register: 'everyday', unsure: false, note: '' },
      { arabic: 'غاب', translit: 'gaab', gloss: 'set, went away', root: 'غ ي ب', register: 'poetic', unsure: true, note: '' },
      // Not in the line: must never be saved.
      { arabic: 'والنجوم', translit: 'wa-n-nujuum', gloss: 'and the stars', root: '', register: 'poetic', unsure: false, note: '' },
    ],
  },
  'النيل طويل': {
    translit: 'an-niil Tawiil', gloss: 'The Nile is long', unsure: false, note: '',
    words: [
      { arabic: 'النيل', translit: 'an-niil', gloss: 'the Nile', root: '', register: 'everyday', unsure: false, note: '' },
      { arabic: 'طويل', translit: 'Tawiil', gloss: 'long', root: 'ط و ل', register: 'everyday', unsure: false, note: '' },
    ],
  },
}

before(async () => {
  app = await startApp()
  const { setDrafter } = await import('../server/drafts')
  setDrafter(async (lines) => {
    requests.push(lines)
    return lines.map((l) => ({ key: l.key, ...DRAFTS[l.arabic] }))
  })
  songId = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Drafts' })).data.id
  lineIds = (await app.call('POST', `/api/songs/${songId}/lines`, { text: Object.keys(DRAFTS).join('\n') })).data.map((l: any) => l.id)
})
after(async () => {
  const { setDrafter } = await import('../server/drafts')
  setDrafter(null)
  await app.close()
})

const detail = async () => (await app.call('GET', `/api/songs/${songId}`)).data

test('drafting fills empty fields as drafts and leaves verified fields untouched', async () => {
  // Line 1: the learner typed the transliteration by hand. Line 3: verified by the tutor.
  await app.call('PATCH', `/api/lines/${lineIds[0]}`, { translit: 'ish-shams Tala3at' })
  await app.call('PATCH', `/api/lines/${lineIds[2]}`, { translit: 'in-niil Tawiil', gloss: 'The Nile is long indeed', status: 'verified' })

  const res = await app.call('POST', '/api/draft', { songId, lineIds })
  assert.equal(res.status, 200, JSON.stringify(res.data))
  const d = await detail()
  const [l1, l2, l3] = d.lines

  assert.equal(l1.translit, 'ish-shams Tala3at', 'hand-typed text is kept')
  assert.equal(l1.gloss, 'The sun came out', 'the empty gloss is filled')
  assert.equal(l1.status, 'draft')
  assert.equal(l1.source, 'claude')

  assert.equal(l2.translit, 'al-gamar gaab')
  assert.equal(l2.unsure, true)
  assert.equal(l2.note, 'gaab: the ghayn is softened here')

  assert.deepEqual([l3.translit, l3.gloss, l3.status, l3.source], ['in-niil Tawiil', 'The Nile is long indeed', 'verified', 'user'], 'verified line untouched')

  const words2 = d.words.filter((w: any) => w.lineId === l2.id)
  assert.deepEqual(words2.map((w: any) => w.arabic), ['القمر', 'غاب'], 'a word that is not in the line is never saved')
  assert.ok(words2.every((w: any) => w.status === 'draft'))
  assert.equal(words2[1].register, 'poetic')
  assert.equal(words2[1].unsure, true)
  assert.ok(res.data.skipped.some((s: string) => s.startsWith('Line 2')), 'the stray word is reported')
})

test('the existing word split is sent to Claude and kept', async () => {
  requests = []
  // The learner fixed a word by hand and verified it.
  const d = await detail()
  const [w1] = d.words.filter((w: any) => w.lineId === lineIds[0])
  await app.call('PATCH', `/api/words/${w1.id}`, { translit: 'ish-shams', gloss: 'sun', status: 'verified' })
  await app.call('POST', '/api/draft', { songId, lineIds: [lineIds[0]] })
  assert.deepEqual(requests[0][0].words, ['الشمس', 'طلعت'])
  const after = (await detail()).words.find((w: any) => w.id === w1.id)
  assert.deepEqual([after.translit, after.gloss, after.status], ['ish-shams', 'sun', 'verified'], 'verified word untouched')
})

test('drafting again does not overwrite earlier drafts', async () => {
  await app.call('PATCH', `/api/lines/${lineIds[1]}`, { gloss: 'The moon went away' })
  await app.call('POST', '/api/draft', { songId, lineIds: [lineIds[1]] })
  assert.equal((await detail()).lines[1].gloss, 'The moon went away')
})

test('verifying a line clears Claude’s doubt', async () => {
  const res = await app.call('PATCH', `/api/lines/${lineIds[1]}`, { status: 'verified' })
  assert.equal(res.data.unsure, false)
  await app.call('PATCH', `/api/lines/${lineIds[1]}`, { status: 'draft' })
})

test('questions attach to a line or a word', async () => {
  const d = await detail()
  const word = d.words.find((w: any) => w.arabic === 'غاب')
  const q1 = await app.call('POST', '/api/questions', { lineId: lineIds[1], text: 'Is this how people say it in Khartoum?' })
  const q2 = await app.call('POST', '/api/questions', { wordId: word.id, text: 'Poetic, or everyday?' })
  assert.equal(q1.status, 200)
  assert.equal(q2.data.lineId, lineIds[1], 'a word question also knows its line')
  assert.equal((await app.call('POST', '/api/questions', { text: 'Floating' })).status, 400)
})

test('the tutor queue lists every draft line, draft word and open question, by song', async () => {
  const queue = (await app.call('GET', '/api/tutor')).data
  assert.equal(queue.length, 1)
  const items = queue[0].lines
  assert.deepEqual(items.map((i: any) => i.line.arabic), ['الشمس طلعت', 'القمر غاب', 'النيل طويل'])
  assert.deepEqual(items.map((i: any) => i.lineIsDraft), [true, true, false], 'the verified line is listed only for its draft words')
  assert.equal(items[2].words.length, 2)
  assert.equal(items[0].words.length, 1, 'only the draft word of line 1')

  // Verifying the words of line 3 takes it off the list.
  for (const { word } of items[2].words) await app.call('PATCH', `/api/words/${word.id}`, { status: 'verified' })
  assert.equal((await app.call('GET', '/api/tutor')).data[0].lines.length, 2)
  assert.equal(items[1].questions.length, 1)
  assert.equal(items[1].words.find((w: any) => w.word.arabic === 'غاب').questions.length, 1)

  // Resolving and verifying take items off the list.
  await app.call('PATCH', `/api/questions/${items[1].questions[0].id}`, { resolved: true })
  const again = (await app.call('GET', '/api/tutor')).data[0].lines
  assert.equal(again[1].questions.length, 0)
})

test('the tutor queue exports to Markdown', async () => {
  const res = await fetch(`${app.url}/api/tutor/export.md`)
  assert.equal(res.status, 200)
  assert.match(res.headers.get('content-type') ?? '', /text\/markdown/)
  assert.match(res.headers.get('content-disposition') ?? '', /attachment; filename="tutor-queue-\d{4}-\d{2}-\d{2}\.md"/)
  const md = await res.text()
  assert.match(md, /^# For my Sudanese Arabic lesson/)
  assert.match(md, /## Drafts/)
  assert.match(md, /### Line 1 _\(Claude draft\)_/)
  assert.match(md, /\*\*Question:\*\* Poetic, or everyday\?/)
  assert.ok(md.includes('⁧القمر غاب⁩'), 'Arabic is isolated so its punctuation keeps its place')
  assert.ok(!md.includes('Is this how people say it'), 'resolved questions are not exported')
  assert.ok(!md.includes('النيل طويل'), 'verified lines with nothing open are not exported')
})

test('with no API key and no stand-in, drafting is refused and the button is hidden', async () => {
  const { setDrafter } = await import('../server/drafts')
  setDrafter(null)
  assert.equal((await app.call('GET', '/api/meta')).data.claude, false)
  const res = await app.call('POST', '/api/draft', { songId, lineIds })
  assert.equal(res.status, 503)
  setDrafter(async (lines) => lines.map((l) => ({ key: l.key, ...DRAFTS[l.arabic] })))
})

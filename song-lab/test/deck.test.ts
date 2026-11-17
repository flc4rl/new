import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startApp, type TestApp } from './helpers'

let app: TestApp
let songId: string
let lineId: string

before(async () => {
  app = await startApp()
  songId = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/AAAAAAAAAAA', title: 'Deck' })).data.id
  // Practice sentences written for these tests, not song lyrics.
  const lines = (await app.call('POST', `/api/songs/${songId}/lines`, { text: 'الشمس طلعت، بدري\nالقمر غاب' })).data
  lineId = lines[0].id
  await app.call('PATCH', `/api/lines/${lineId}`, { translit: 'ash-shamis Tala3at badri', gloss: 'The sun rose early', startSec: 10, endSec: 14 })
  await app.call('PATCH', `/api/songs/${songId}`, { savedRate: 0.75 })
})
after(() => app.close())

const detail = async () => (await app.call('GET', `/api/songs/${songId}`)).data
const wordsOf = async (id: string) => (await detail()).words.filter((w: any) => w.lineId === id)

test('a line splits into words, without punctuation', async () => {
  const res = await app.call('POST', `/api/lines/${lineId}/split`)
  assert.deepEqual(res.data.map((w: any) => w.arabic), ['الشمس', 'طلعت', 'بدري'])
  assert.ok(res.data.every((w: any) => w.register === 'unknown' && w.status === 'draft'))
})

test('a word carries transliteration, meaning, root, register and a note', async () => {
  const [word] = await wordsOf(lineId)
  const res = await app.call('PATCH', `/api/words/${word.id}`, {
    translit: 'ash-shamis', gloss: 'the sun', root: 'ش م س', register: 'everyday', note: 'sun letter',
  })
  assert.deepEqual(
    [res.data.translit, res.data.gloss, res.data.root, res.data.register, res.data.note],
    ['ash-shamis', 'the sun', 'ش م س', 'everyday', 'sun letter'],
  )
  assert.equal((await app.call('PATCH', `/api/words/${word.id}`, { register: 'slang' })).status, 400)
})

test('splitting again keeps the words that still match', async () => {
  const before = await wordsOf(lineId)
  await app.call('PATCH', `/api/lines/${lineId}`, { arabic: 'الشمس طلعت اليوم بدري' })
  const after = (await app.call('POST', `/api/lines/${lineId}/split`)).data
  assert.deepEqual(after.map((w: any) => w.arabic), ['الشمس', 'طلعت', 'اليوم', 'بدري'])
  assert.equal(after[0].id, before[0].id)
  assert.equal(after[0].translit, 'ash-shamis', 'the kept word still has its transliteration')
  assert.equal(after[3].id, before[2].id)
  assert.deepEqual(after.map((w: any) => w.order), [0, 1, 2, 3])
})

test('one request turns a word into cards in both directions, once', async () => {
  const [word] = await wordsOf(lineId)
  const made = await app.call('POST', '/api/cards', { card: { kind: 'word', wordId: word.id } })
  assert.deepEqual(made.data.map((c: any) => c.direction), ['ar-en', 'en-ar'])
  const card = made.data[0]
  assert.equal(card.arabic, 'الشمس')
  assert.equal(card.gloss, 'the sun')
  assert.equal(card.isNew, true)
  assert.equal(card.lineArabic, 'الشمس طلعت اليوم بدري')
  assert.deepEqual(card.clip, { youtubeId: 'AAAAAAAAAAA', startSec: 10, endSec: 14, rate: 0.75 })
  const again = await app.call('POST', '/api/cards', { card: { kind: 'word', wordId: word.id } })
  assert.equal(again.data.length, 0, 'no duplicates')
})

test('a song card reads its text from the word it points to', async () => {
  const [word] = await wordsOf(lineId)
  await app.call('PATCH', `/api/words/${word.id}`, { gloss: 'sun' })
  const cards = (await app.call('GET', '/api/cards')).data
  assert.ok(cards.filter((c: any) => c.wordId === word.id).every((c: any) => c.gloss === 'sun'))
})

test('a line becomes a card, and a phrase can be added by hand without audio', async () => {
  const line = await app.call('POST', '/api/cards', { card: { kind: 'line', lineId }, directions: ['ar-en'] })
  assert.equal(line.data.length, 1)
  assert.equal(line.data[0].translit, 'ash-shamis Tala3at badri')
  const phrase = await app.call('POST', '/api/cards', {
    card: { kind: 'phrase', arabic: 'كيف الحال', translit: 'keef al-Haal', gloss: 'How are you?' },
  })
  assert.equal(phrase.data.length, 2)
  assert.equal(phrase.data[0].clip, null)
  assert.equal(phrase.data[0].status, null)
  assert.equal((await app.call('POST', '/api/cards', { card: { kind: 'phrase', arabic: '', gloss: 'x' } })).status, 400)
})

test('the queue offers new cards up to the daily limit, one direction of an item per day', async () => {
  const all = (await app.call('GET', '/api/cards')).data
  assert.equal(all.length, 5) // word x2, line x1, phrase x2
  const queue = (await app.call('GET', '/api/review/queue')).data
  assert.equal(queue.dueCount, 0)
  assert.equal(queue.newLeftToday, 10)
  assert.equal(queue.cards.length, 3, 'three items; the second direction of each waits for another day')
  assert.deepEqual(queue.cards.map((c: any) => c.kind).sort(), ['line', 'phrase', 'word'])

  await app.call('PUT', '/api/settings', { newPerDay: 2 })
  assert.equal((await app.call('GET', '/api/review/queue')).data.cards.length, 2)
  await app.call('PUT', '/api/settings', { newPerDay: 10 })
})

test('a card graded again returns in the same session; good sends it to another day', async () => {
  const queue = (await app.call('GET', '/api/review/queue')).data
  const first = queue.cards[0]
  const again = await app.call('POST', '/api/review', { cardId: first.id, grade: 'again' })
  assert.equal(again.data.requeue, true)
  assert.equal(again.data.card.isNew, false)
  const afterAgain = (await app.call('GET', '/api/review/queue')).data
  assert.ok(afterAgain.cards.some((c: any) => c.id === first.id), 'still in the queue')
  assert.equal(afterAgain.dueCount, 1)
  assert.equal(afterAgain.newLeftToday, 9, 'it counts against today’s new cards')

  const good = await app.call('POST', '/api/review', { cardId: first.id, grade: 'good' })
  assert.equal(good.data.requeue, false)
  assert.equal(good.data.card.interval, 1)
  const afterGood = (await app.call('GET', '/api/review/queue')).data
  assert.ok(!afterGood.cards.some((c: any) => c.id === first.id), 'gone until tomorrow')
})

test('a suspended card is left out of the queue', async () => {
  const queue = (await app.call('GET', '/api/review/queue')).data
  const target = queue.cards[0]
  await app.call('PATCH', `/api/cards/${target.id}`, { suspended: true })
  const after = (await app.call('GET', '/api/review/queue')).data
  assert.ok(!after.cards.some((c: any) => c.id === target.id))
  await app.call('PATCH', `/api/cards/${target.id}`, { suspended: false })
})

test('deleting a word keeps its cards as stand-alone cards with the line clip', async () => {
  const [word] = await wordsOf(lineId)
  await app.call('DELETE', `/api/words/${word.id}`)
  const cards = (await app.call('GET', '/api/cards')).data.filter((c: any) => c.kind === 'word')
  assert.equal(cards.length, 2)
  assert.ok(cards.every((c: any) => c.wordId === null && c.arabic === 'الشمس' && c.gloss === 'sun' && c.clip))
  assert.deepEqual((await wordsOf(lineId)).map((w: any) => w.order), [0, 1, 2])
})

test('deleting a song keeps or removes its cards, as asked', async () => {
  const keep = (await app.call('POST', '/api/songs', { url: 'https://youtu.be/BBBBBBBBBBB', title: 'Keep' })).data
  const [line] = (await app.call('POST', `/api/songs/${keep.id}/lines`, { text: 'النيل طويل' })).data
  await app.call('PATCH', `/api/lines/${line.id}`, { gloss: 'The Nile is long', startSec: 1 })
  await app.call('POST', '/api/cards', { card: { kind: 'line', lineId: line.id }, directions: ['ar-en'] })
  await app.call('DELETE', `/api/songs/${keep.id}?keepCards=1`)
  let cards = (await app.call('GET', '/api/cards')).data
  const kept = cards.find((c: any) => c.arabic === 'النيل طويل')
  assert.ok(kept, 'the card survives')
  assert.deepEqual([kept.lineId, kept.clip, kept.gloss], [null, null, 'The Nile is long'])

  const countBefore = cards.length
  await app.call('DELETE', `/api/songs/${songId}?keepCards=0`)
  cards = (await app.call('GET', '/api/cards')).data
  // The song's line card and its detached word cards go; the phrase cards and the kept card stay.
  assert.equal(cards.length, countBefore - 3)
  assert.equal((await app.call('GET', `/api/songs/${songId}`)).status, 404)
})

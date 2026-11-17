import assert from 'node:assert/strict'
import { test } from 'node:test'
import { previewInterval, schedule, START_EASE, type SrsState } from '../shared/srs'

const NOW = new Date(2026, 10, 16, 21, 30) // a late-evening review
const fresh: SrsState = { interval: 0, ease: START_EASE, reps: 0, lapses: 0 }
const minutesAway = (d: Date) => Math.round((d.getTime() - NOW.getTime()) / 60_000)
const midnight = (daysAhead: number) => new Date(2026, 10, 16 + daysAhead)

test('a new card graded again or hard comes back within minutes, in the same session', () => {
  const again = schedule(fresh, 'again', NOW)
  assert.equal(again.requeue, true)
  assert.equal(minutesAway(again.due), 1)
  assert.equal(again.reps, 0)
  assert.equal(again.lapses, 0, 'failing a card that was never learned is not a lapse')
  const hard = schedule(fresh, 'hard', NOW)
  assert.equal(hard.requeue, true)
  assert.equal(minutesAway(hard.due), 6)
})

test('good graduates a new card to tomorrow, easy to four days', () => {
  const good = schedule(fresh, 'good', NOW)
  assert.deepEqual([good.interval, good.reps, good.requeue], [1, 1, false])
  assert.equal(good.due.getTime(), midnight(1).getTime(), 'due at the start of the day, whatever the hour of the review')
  const easy = schedule(fresh, 'easy', NOW)
  assert.equal(easy.interval, 4)
  assert.equal(easy.due.getTime(), midnight(4).getTime())
  assert.ok(easy.ease > START_EASE)
})

test('intervals grow with each successful review', () => {
  let state: SrsState = fresh
  const intervals: number[] = []
  for (let i = 0; i < 5; i++) {
    state = schedule(state, 'good', NOW)
    intervals.push(state.interval)
  }
  assert.deepEqual(intervals, [1, 3, 8, 20, 50])
})

test('hard grows slowly and lowers ease; easy grows fast and raises it', () => {
  const learned: SrsState = { interval: 10, ease: 2.5, reps: 3, lapses: 0 }
  const hard = schedule(learned, 'hard', NOW)
  const good = schedule(learned, 'good', NOW)
  const easy = schedule(learned, 'easy', NOW)
  assert.deepEqual([hard.interval, good.interval, easy.interval], [12, 25, 33])
  assert.ok(hard.ease < 2.5 && easy.ease > 2.5 && good.ease === 2.5)
})

test('again on a learned card is a lapse: it returns now and relearns from one day', () => {
  const learned: SrsState = { interval: 20, ease: 2.5, reps: 4, lapses: 0 }
  const lapsed = schedule(learned, 'again', NOW)
  assert.deepEqual([lapsed.reps, lapsed.lapses, lapsed.interval, lapsed.requeue], [0, 1, 0, true])
  assert.equal(lapsed.ease, 2.3)
  const relearned = schedule(lapsed, 'good', NOW)
  assert.deepEqual([relearned.interval, relearned.reps, relearned.lapses], [1, 1, 1])
})

test('ease never drops below 1.3', () => {
  let state: SrsState = { interval: 5, ease: 1.35, reps: 2, lapses: 0 }
  state = schedule(state, 'again', NOW)
  assert.equal(state.ease, 1.3)
})

test('grade buttons preview when the card returns', () => {
  assert.equal(previewInterval(fresh, 'again', NOW), '1 min')
  assert.equal(previewInterval(fresh, 'good', NOW), '1 d')
  assert.equal(previewInterval({ interval: 30, ease: 2.5, reps: 5, lapses: 0 }, 'good', NOW), '3 mo')
})

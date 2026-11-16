import assert from 'node:assert/strict'
import { test } from 'node:test'
import { activeLineAt, lineEnd, nudge } from '../src/lib/timing'
import type { Line } from '../shared/types'

function line(id: string, startSec: number | null, endSec: number | null = null): Line {
  return { id, songId: 's', order: 0, arabic: '', translit: '', gloss: '', startSec, endSec, status: 'draft', source: 'user', note: '', unsure: false }
}

const lines = [line('a', 2, 4), line('b', 4, 6.5), line('c', 6.5), line('d', null), line('e', 12)]

test('the active line is the last one that has started', () => {
  assert.equal(activeLineAt(lines, 0.5), null)
  assert.equal(activeLineAt(lines, 2)?.id, 'a')
  assert.equal(activeLineAt(lines, 3.95)?.id, 'b', 'the highlight leads the voice by a tenth of a second')
  assert.equal(activeLineAt(lines, 9)?.id, 'c')
  assert.equal(activeLineAt(lines, 100)?.id, 'e')
})

test('a line with no end of its own ends where the next timed line begins', () => {
  assert.equal(lineEnd(lines, lines[0]), 4)
  assert.equal(lineEnd(lines, lines[2]), 12, 'untimed lines in between are skipped')
  assert.equal(lineEnd(lines, lines[4]), 18, 'the last line falls back to a fixed length')
  assert.equal(lineEnd(lines, lines[3]), null)
})

test('nudging a start takes the joined end of the previous line with it', () => {
  assert.deepEqual(nudge(lines, lines[1], 'start', -0.25), [
    { id: 'b', startSec: 3.75 },
    { id: 'a', endSec: 3.75 },
  ])
})

test('nudging a start leaves a previous line alone when there was a gap', () => {
  const gapped = [line('a', 2, 3.5), line('b', 4, 6)]
  assert.deepEqual(nudge(gapped, gapped[1], 'start', 0.25), [{ id: 'b', startSec: 4.25 }])
})

test('nudging an end moves only that line', () => {
  assert.deepEqual(nudge(lines, lines[0], 'end', 0.25), [{ id: 'a', endSec: 4.25 }])
  assert.deepEqual(nudge(lines, lines[2], 'end', -0.25), [{ id: 'c', endSec: 11.75 }], 'an inferred end becomes an explicit one')
})

test('nudges cannot push a line before zero or turn it inside out', () => {
  const early = [line('a', 0.1, 1)]
  assert.deepEqual(nudge(early, early[0], 'start', -0.25), [{ id: 'a', startSec: 0 }])
  const short = [line('a', 1, 1.2)]
  assert.deepEqual(nudge(short, short[0], 'end', -0.25), [{ id: 'a', endSec: 1.1 }])
  assert.deepEqual(nudge(short, short[0], 'start', 0.25), [{ id: 'a', startSec: 1.1 }])
  assert.deepEqual(nudge(lines, lines[3], 'start', 0.25), [], 'an untimed line cannot be nudged')
})

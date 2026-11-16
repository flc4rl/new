import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseYouTubeId, watchUrl } from '../shared/youtube'
import { formatTime, splitLines, splitWords } from '../shared/text'

test('parseYouTubeId reads the common link shapes', () => {
  const id = 'dQw4w9WgXcQ'
  for (const link of [
    id,
    `https://www.youtube.com/watch?v=${id}`,
    `https://www.youtube.com/watch?v=${id}&list=PL123&t=42s`,
    `https://youtu.be/${id}?si=abc`,
    `https://m.youtube.com/watch?v=${id}`,
    `https://music.youtube.com/watch?v=${id}`,
    `https://www.youtube.com/embed/${id}`,
    `https://www.youtube.com/shorts/${id}`,
    `https://www.youtube.com/live/${id}`,
    `youtube.com/watch?v=${id}`,
    `  https://youtu.be/${id}  `,
  ]) {
    assert.equal(parseYouTubeId(link), id, link)
  }
})

test('parseYouTubeId rejects other input', () => {
  for (const link of ['', 'hello', 'https://example.com/watch?v=dQw4w9WgXcQ', 'https://www.youtube.com/', 'https://vimeo.com/123']) {
    assert.equal(parseYouTubeId(link), null, link)
  }
})

test('watchUrl links to the video, optionally at a time', () => {
  assert.equal(watchUrl('dQw4w9WgXcQ'), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  assert.equal(watchUrl('dQw4w9WgXcQ', 83.7), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=83s')
})

test('splitLines keeps one line per row and drops blank rows', () => {
  assert.deepEqual(splitLines('one\r\n\n  two   words \n\nthree'), ['one', 'two words', 'three'])
})

test('splitWords splits on spaces and strips punctuation at the edges', () => {
  assert.deepEqual(splitWords('الشمس  طلعت، والقمر غاب!'), ['الشمس', 'طلعت', 'والقمر', 'غاب'])
  assert.deepEqual(splitWords('«كيف الحال؟»'), ['كيف', 'الحال'])
  // Vowel marks belong to the word.
  assert.deepEqual(splitWords('قَلْب'), ['قَلْب'])
  assert.deepEqual(splitWords('  …  '), [])
})

test('formatTime shows minutes, seconds and tenths', () => {
  assert.equal(formatTime(0), '0:00.0')
  assert.equal(formatTime(83.44), '1:23.4')
  assert.equal(formatTime(9.96), '0:10.0')
  assert.equal(formatTime(240, 0), '4:00')
  assert.equal(formatTime(null), '–:––')
})

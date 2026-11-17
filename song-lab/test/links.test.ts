import assert from 'node:assert/strict'
import { test } from 'node:test'
import { linkArabic, linkGloss, linkTranslit, type Segment } from '../src/lib/links'
import type { Word } from '../shared/types'

function word(id: string, arabic: string, translit: string, gloss: string, glossPart = ''): Word {
  return { id, lineId: 'l', order: 0, arabic, translit, gloss, root: '', register: 'unknown', status: 'draft', note: '', glossPart, unsure: false }
}

// Practice sentences written for these tests, not song lyrics.
const words = [
  word('a', 'القمر', 'al-gamar', 'the moon', 'The moon'),
  word('b', 'غاب', 'gaab', 'set', 'has set'),
  word('c', 'يا', 'ya', 'O'),
  word('d', 'حبيبي', 'Habiibi', 'my love'),
]

const linked = (segments: Segment[]) => segments.filter((s) => s.wordId).map((s) => `${s.wordId}:${s.text}`)
const joined = (segments: Segment[]) => segments.map((s) => s.text).join('')

test('every character of the line is kept', () => {
  const line = 'القمر غاب،  يا حبيبي!'
  assert.equal(joined(linkArabic(line, words)), line)
  assert.equal(joined(linkGloss('The moon has set, my love!', words)), 'The moon has set, my love!')
})

test('Arabic words link to their word, punctuation stays outside', () => {
  assert.deepEqual(linked(linkArabic('القمر غاب، يا حبيبي!', words)), ['a:القمر', 'b:غاب', 'c:يا', 'd:حبيبي'])
})

test('vowel marks and tatweel do not stop a match', () => {
  assert.deepEqual(linked(linkArabic('القَمَر غـاب', words.slice(0, 2))), ['a:القَمَر', 'b:غـاب'])
})

test('transliteration links by each word’s transliteration', () => {
  assert.deepEqual(linked(linkTranslit('al-gamar gaab, ya Habiibi', words)), ['a:al-gamar', 'b:gaab', 'c:ya', 'd:Habiibi'])
})

test('a transliteration typed differently still links by position when the counts agree', () => {
  assert.deepEqual(linked(linkTranslit('il-gamar ghaab yaa Habibi', words)), ['a:il-gamar', 'b:ghaab', 'c:yaa', 'd:Habibi'])
})

test('English links through glossPart, then through the word’s own meaning', () => {
  assert.deepEqual(linked(linkGloss('The moon has set, my love', words)), ['a:The moon', 'b:has set', 'd:my love'])
})

test('English matches whole words only, and never twice', () => {
  const two = [word('x', 'ال', '', 'on'), word('y', 'بيت', '', 'house')]
  assert.deepEqual(linked(linkGloss('Once more, the house', two)), ['y:house'], '"on" inside "Once" is not a match')
})

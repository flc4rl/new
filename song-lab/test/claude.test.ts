import assert from 'node:assert/strict'
import { test } from 'node:test'
import { systemPrompt, userPrompt } from '../server/claude'
import { normalizeArabic } from '../server/drafts'
import { loadTranslitConfig } from '../server/config'

test('the prompt carries the whole transliteration table and asks for Khartoum pronunciation', () => {
  const config = loadTranslitConfig()
  const prompt = systemPrompt(config)
  for (const letter of config.letters) assert.ok(prompt.includes(`${letter.arabic} → ${letter.write}`), letter.arabic)
  assert.match(prompt, /Khartoum/)
  assert.match(prompt, /unsure/)
  assert.match(prompt, /empty string rather than guess/)
  assert.match(prompt, /Never add, complete, correct, continue or quote lyrics/)
})

test('the request names each line by a short key and passes an existing split', () => {
  const text = userPrompt(
    [
      { key: 'L1', arabic: 'الشمس طلعت', words: ['الشمس', 'طلعت'] },
      { key: 'L2', arabic: 'القمر غاب', words: [] },
    ],
    { title: 'A song', artist: '' },
  )
  assert.match(text, /^Song title: A song\n/)
  const payload = JSON.parse(text.slice(text.indexOf('[')))
  assert.deepEqual(payload, [
    { key: 'L1', arabic: 'الشمس طلعت', words: ['الشمس', 'طلعت'] },
    { key: 'L2', arabic: 'القمر غاب' },
  ])
})

test('Arabic is compared without vowel marks, tatweel or punctuation', () => {
  assert.equal(normalizeArabic('قَلْبِي،'), 'قلبي')
  assert.equal(normalizeArabic('حبيـــبي'), 'حبيبي')
})

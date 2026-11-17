import { formatTime, isolateRtl } from '../shared/text'
import { watchUrl } from '../shared/youtube'
import type { Question, TutorLineItem, TutorSongGroup, Word } from '../shared/types'

// Arabic is wrapped in direction isolates so that, mixed with Latin text, its punctuation stays in place.
const ar = isolateRtl

function flags(item: { status: string; unsure: boolean }, source?: string): string {
  const parts: string[] = []
  if (item.status === 'draft') parts.push(source === 'claude' ? 'Claude draft' : source === 'google' ? 'Google draft' : 'draft')
  if (item.unsure) parts.push('Claude unsure')
  return parts.length ? ` _(${parts.join(', ')})_` : ''
}

function questionLines(questions: Question[], indent = ''): string[] {
  return questions.map((q) => `${indent}- **Question:** ${q.text.replace(/\s+/g, ' ').trim()}`)
}

function wordLine(word: Word): string {
  const details = [
    word.translit ? `\`${word.translit}\`` : '_no transliteration_',
    word.gloss || '_no meaning_',
    word.root ? `root ${ar(word.root)}` : '',
    word.register !== 'unknown' ? word.register : 'register unknown',
  ].filter(Boolean)
  const note = word.note ? ` · ${word.note}` : ''
  return `- ${ar(word.arabic)} · ${details.join(' · ')}${note}${flags(word)}`
}

function lineBlock(item: TutorLineItem, number: number, youtubeId: string): string[] {
  const { line } = item
  const at = line.startSec != null ? ` · [${formatTime(line.startSec, 0)}](${watchUrl(youtubeId, line.startSec)})` : ''
  const out = [`### Line ${number}${at}${item.lineIsDraft ? flags(line, line.source) : ''}`, '']
  out.push(`> ${ar(line.arabic) || '_(no Arabic)_'}`, '')
  out.push(`- **Transliteration:** ${line.translit ? `\`${line.translit}\`` : '_empty_'}`)
  out.push(`- **English:** ${line.gloss || '_empty_'}`)
  if (line.note) out.push(`- **Note:** ${line.note}`)
  out.push(...questionLines(item.questions))
  if (item.words.length > 0) {
    out.push('', 'Words to check:', '')
    for (const { word, questions } of item.words) {
      out.push(wordLine(word))
      out.push(...questionLines(questions, '  '))
    }
  }
  out.push('')
  return out
}

/** The tutor queue as a Markdown document, to send before a lesson. */
export function tutorMarkdown(groups: TutorSongGroup[], lineNumbers: Map<string, number>, now = new Date()): string {
  const date = now.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
  const out = ['# For my Sudanese Arabic lesson', '', `Questions and drafts to check, ${date}.`, '']
  if (groups.length === 0) {
    out.push('Nothing to check: every line and word is verified and every question is answered.', '')
    return out.join('\n')
  }
  const counts = groups.flatMap((g) => g.lines).reduce(
    (acc, item) => {
      acc.lines += item.lineIsDraft ? 1 : 0
      acc.words += item.words.filter((w) => w.word.status === 'draft').length
      acc.questions += item.questions.length + item.words.reduce((n, w) => n + w.questions.length, 0)
      return acc
    },
    { lines: 0, words: 0, questions: 0 },
  )
  out.push(`${counts.questions} questions, ${counts.lines} draft lines and ${counts.words} draft words.`, '')
  out.push('Transliteration: capitals mark emphatic consonants and the deep h (S D T Z H); 3 is ع and \' is the glottal stop.', '')
  for (const group of groups) {
    const by = group.song.artist ? ` · ${group.song.artist}` : ''
    out.push(`## ${group.song.title}${by}`, '', watchUrl(group.song.youtubeId), '')
    for (const item of group.lines) {
      out.push(...lineBlock(item, lineNumbers.get(item.line.id) ?? item.line.order + 1, group.song.youtubeId))
    }
  }
  return out.join('\n')
}

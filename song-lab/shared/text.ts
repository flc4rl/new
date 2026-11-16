// Punctuation, symbols and tatweel at the edges of a token are not part of the word.
const EDGES = /^[\p{P}\p{S}\u0640]+|[\p{P}\p{S}\u0640]+$/gu

/** Split a lyric line into words on whitespace. Vowel marks inside a word are kept. */
export function splitWords(arabic: string): string[] {
  return arabic
    .split(/\s+/u)
    .map((token) => token.replace(EDGES, ''))
    .filter((token) => token.length > 0)
}

/** Turn pasted lyrics into lines: one per row, blank rows dropped. */
export function splitLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((row) => row.replace(/\s+/g, ' ').trim())
    .filter((row) => row.length > 0)
}

/** 83.4 -> "1:23.4" */
export function formatTime(sec: number | null | undefined, decimals = 1): string {
  if (sec == null || !Number.isFinite(sec)) return '–:––'
  // Round first, so 9.96 reads 0:10.0 and never 0:010.0.
  const factor = 10 ** decimals
  const total = decimals > 0 ? Math.round(Math.max(0, sec) * factor) / factor : Math.floor(Math.max(0, sec))
  const m = Math.floor(total / 60)
  const rest = total - m * 60
  return `${m}:${rest < 10 ? '0' : ''}${rest.toFixed(decimals)}`
}

/** Wrap right-to-left text so neighbouring Latin text and punctuation keep their order in plain text. */
export function isolateRtl(text: string): string {
  return text ? `\u2067${text}\u2069` : text
}

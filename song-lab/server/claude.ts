import Anthropic from '@anthropic-ai/sdk'
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod'
import { z } from 'zod'
import { CLAUDE_MODEL, loadTranslitConfig } from './config'
import { HttpError } from './http'
import type { TranslitConfig } from '../shared/types'

/** What Claude sends back for one line. Every field is checked against this schema before anything is saved. */
export const DraftWordSchema = z.object({
  arabic: z.string(),
  translit: z.string(),
  gloss: z.string(),
  root: z.string(),
  register: z.enum(['everyday', 'poetic', 'unknown']),
  unsure: z.boolean(),
  note: z.string(),
})

export const DraftLineSchema = z.object({
  key: z.string(),
  translit: z.string(),
  gloss: z.string(),
  unsure: z.boolean(),
  note: z.string(),
  words: z.array(DraftWordSchema),
})

const DraftResponseSchema = z.object({ lines: z.array(DraftLineSchema) })

export type DraftWord = z.infer<typeof DraftWordSchema>
export type DraftLine = z.infer<typeof DraftLineSchema>

export interface DraftRequestLine {
  /** A short key such as "L1"; the caller maps it back to the line. */
  key: string
  arabic: string
  /** The words the line is already split into, if any. Claude keeps this split. */
  words: string[]
}

export interface DraftContext {
  title: string
  artist: string
}

/** Something that turns lines into drafts. The real one calls Claude; tests swap in a stand-in. */
export type Drafter = (lines: DraftRequestLine[], context: DraftContext) => Promise<DraftLine[]>

function schemeText(config: TranslitConfig): string {
  const rows = config.letters.map((l) => `${l.arabic} → ${l.write}  (${l.sound})`).join('\n')
  return [
    ...config.rules.map((rule) => `- ${rule}`),
    '',
    'Letters that differ from a plain reading:',
    rows,
    config.otherConsonants,
  ].join('\n')
}

export function systemPrompt(config: TranslitConfig): string {
  return `You help one adult beginner learn ${config.dialect} from songs. He types the lyrics in himself; a Sudanese tutor checks your work later, and everything you write is saved as a draft until he or the tutor confirms it.

Your job, for each line you are given:
- translit: how a singer from Khartoum would pronounce the line, in the transliteration scheme below. Use Khartoum pronunciation, not Modern Standard Arabic: for example ق is usually g.
- gloss: a plain, natural English meaning of the line.
- words: the line's words in order, each with its own translit, gloss (meaning in context), root, register, unsure flag and note.

Transliteration scheme:
${schemeText(config)}

Word split:
- When a line comes with words, use exactly those words, in that order, spelled as given.
- Otherwise split the line on spaces, exactly as written. Keep attached letters such as و, ب, ل, ف and the article with their word. Leave out punctuation.

Fields:
- root: the root consonants in Arabic letters separated by spaces (for example ش م س), only when you are confident. Otherwise an empty string.
- register: "everyday" if the word is used in ordinary Khartoum speech, "poetic" if it is poetic, literary, Modern Standard or old-fashioned and would sound odd in conversation, "unknown" if you cannot tell.
- note: one short sentence only when it helps the learner (a Sudanese form, a poetic form, a difference from Modern Standard Arabic). Otherwise an empty string.

Honesty:
- Set unsure to true for any word or line you are not confident about: a dialect word you do not know, a meaning that depends on context you lack, or vowels you cannot be sure of.
- Leave a field as an empty string rather than guess.

Limits:
- Work only on the lines you are given. Never add, complete, correct, continue or quote lyrics that are not in the request, and never change the Arabic of a line or a word.
- Return exactly one entry for each line, with the same key.`
}

export function userPrompt(lines: DraftRequestLine[], context: DraftContext): string {
  const song = [context.title && `Song title: ${context.title}`, context.artist && `Artist: ${context.artist}`]
    .filter(Boolean)
    .join('\n')
  const payload = lines.map((line) => (line.words.length ? { key: line.key, arabic: line.arabic, words: line.words } : { key: line.key, arabic: line.arabic }))
  return `${song ? `${song}\n\n` : ''}Lines:\n${JSON.stringify(payload, null, 1)}`
}

let client: Anthropic | null = null

function getClient(): Anthropic {
  // The key is read from ANTHROPIC_API_KEY on the server. It never reaches the browser.
  client ??= new Anthropic()
  return client
}

/** Ask Claude for transliteration, gloss and words for a batch of lines. */
export const claudeDrafter: Drafter = async (lines, context) => {
  const config = loadTranslitConfig()
  let message
  try {
    message = await getClient().beta.messages.parse({
      model: CLAUDE_MODEL,
      max_tokens: 16000,
      // If the model declines, the API retries the request on a suitable fallback model.
      betas: ['server-side-fallback-2026-07-01'],
      fallbacks: 'default',
      system: systemPrompt(config),
      messages: [{ role: 'user', content: userPrompt(lines, context) }],
      output_config: { effort: 'medium', format: betaZodOutputFormat(DraftResponseSchema) },
    })
  } catch (err) {
    throw describeApiError(err)
  }
  if (message.stop_reason === 'refusal') {
    throw new HttpError(502, 'Claude declined to draft these lines. Try fewer lines at a time, or fill them in by hand.')
  }
  if (message.stop_reason === 'max_tokens') {
    throw new HttpError(502, 'The answer was cut off. Try fewer lines at a time.')
  }
  if (!message.parsed_output) {
    throw new HttpError(502, 'Claude’s answer could not be read. Nothing was saved. Try again.')
  }
  return message.parsed_output.lines
}

function describeApiError(err: unknown): HttpError {
  if (err instanceof Anthropic.AuthenticationError) {
    return new HttpError(502, 'The Anthropic API key was not accepted. Check ANTHROPIC_API_KEY and restart the app.')
  }
  if (err instanceof Anthropic.PermissionDeniedError) {
    return new HttpError(502, 'The Anthropic API key is not allowed to use this model. Check the key, or set SONGLAB_MODEL.')
  }
  if (err instanceof Anthropic.NotFoundError) {
    return new HttpError(502, `The model “${CLAUDE_MODEL}” was not found. Set SONGLAB_MODEL to a current model name.`)
  }
  if (err instanceof Anthropic.RateLimitError) {
    return new HttpError(429, 'Too many requests to Claude just now. Wait a minute and try again.')
  }
  if (err instanceof Anthropic.BadRequestError) {
    return new HttpError(502, `Claude could not take this request: ${err.message}`)
  }
  if (err instanceof Anthropic.APIConnectionError) {
    return new HttpError(502, 'Claude could not be reached. Check the internet connection.')
  }
  if (err instanceof Anthropic.APIError) {
    return new HttpError(502, `Claude answered with an error (${err.status ?? 'no status'}). Try again later.`)
  }
  return err instanceof HttpError ? err : new HttpError(502, 'The request to Claude failed.')
}

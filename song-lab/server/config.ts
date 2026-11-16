import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { TranslitConfig } from '../shared/types'

/** The project folder, wherever the server was started from. */
export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// A .env file in the project folder is the easy way to set the API key on any system.
const envFile = path.join(ROOT, '.env')
if (fs.existsSync(envFile)) process.loadEnvFile(envFile)

export const PORT = Number(process.env.PORT) || 5177

/** The model used for drafts. Change it here or with SONGLAB_MODEL when a newer one is out. */
export const CLAUDE_MODEL = process.env.SONGLAB_MODEL || 'claude-opus-5-5'

/** Drafting is offered only when the server has a key. The key never leaves the server. */
export function claudeAvailable(): boolean {
  return Boolean(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN)
}

/** The transliteration scheme lives in one file so it can follow the tutor or the coursebook. */
export function loadTranslitConfig(): TranslitConfig {
  return JSON.parse(fs.readFileSync(path.join(ROOT, 'translit.config.json'), 'utf8')) as TranslitConfig
}

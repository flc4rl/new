import type {
  CardView,
  Direction,
  DraftResult,
  Grade,
  Line,
  Meta,
  Question,
  Recording,
  Register,
  ReviewQueue,
  Settings,
  Song,
  SongDetail,
  SongSummary,
  Source,
  Status,
  TutorSongGroup,
  Word,
} from '../shared/types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: { blob: Blob }): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: raw
        ? { 'Content-Type': raw.blob.type || 'application/octet-stream' }
        : body !== undefined
          ? { 'Content-Type': 'application/json' }
          : undefined,
      body: raw ? raw.blob : body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'The app is not answering. Check that it is still running in the terminal.')
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, data?.error ?? `The request failed (${res.status}).`)
  return data as T
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body: unknown = {}) => request<T>('POST', path, body)
const patch = <T>(path: string, body: unknown) => request<T>('PATCH', path, body)
const put = <T>(path: string, body: unknown) => request<T>('PUT', path, body)
const del = <T = void>(path: string) => request<T>('DELETE', path)

export interface YouTubeLookup {
  youtubeId: string | null
  title: string | null
  author: string | null
  available: boolean | null
}

export interface LinePatch {
  arabic?: string
  translit?: string
  gloss?: string
  startSec?: number | null
  endSec?: number | null
  status?: Status
  source?: Source
  note?: string
}

export interface WordPatch {
  arabic?: string
  translit?: string
  gloss?: string
  root?: string
  register?: Register
  status?: Status
  note?: string
}

export type NewCard =
  | { kind: 'word'; wordId: string }
  | { kind: 'line'; lineId: string }
  | { kind: 'phrase'; arabic: string; translit: string; gloss: string }

export const api = {
  meta: () => get<Meta>('/api/meta'),
  settings: () => get<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => put<Settings>('/api/settings', s),

  songs: () => get<SongSummary[]>('/api/songs'),
  song: (id: string) => get<SongDetail>(`/api/songs/${id}`),
  addSong: (s: { url: string; title: string; artist: string; notes: string }) => post<Song>('/api/songs', s),
  updateSong: (id: string, s: Partial<{ title: string; artist: string; notes: string; savedRate: number; url: string }>) =>
    patch<Song>(`/api/songs/${id}`, s),
  deleteSong: (id: string, keepCards: boolean) => del(`/api/songs/${id}?keepCards=${keepCards ? 1 : 0}`),
  lookup: (url: string) => get<YouTubeLookup>(`/api/youtube/lookup?url=${encodeURIComponent(url)}`),

  addLines: (songId: string, text: string) => post<Line[]>(`/api/songs/${songId}/lines`, { text }),
  addBlankLine: (songId: string, afterLineId: string) =>
    post<Line[]>(`/api/songs/${songId}/lines`, { blank: true, afterLineId }),
  updateLine: (id: string, p: LinePatch) => patch<Line>(`/api/lines/${id}`, p),
  deleteLine: (id: string) => del(`/api/lines/${id}`),
  moveLine: (id: string, delta: -1 | 1) => post<Line[]>(`/api/lines/${id}/move`, { delta }),
  clearTimings: (songId: string) => post<void>(`/api/songs/${songId}/clear-timings`),

  splitLine: (lineId: string) => post<Word[]>(`/api/lines/${lineId}/split`),
  addWord: (lineId: string, arabic: string) => post<Word>(`/api/lines/${lineId}/words`, { arabic }),
  updateWord: (id: string, p: WordPatch) => patch<Word>(`/api/words/${id}`, p),
  deleteWord: (id: string) => del(`/api/words/${id}`),

  cards: () => get<CardView[]>('/api/cards'),
  addCards: (card: NewCard, directions?: Direction[]) => post<CardView[]>('/api/cards', { card, directions }),
  updateCard: (id: string, p: { suspended?: boolean; arabic?: string; translit?: string; gloss?: string }) =>
    patch<CardView>(`/api/cards/${id}`, p),
  deleteCard: (id: string) => del(`/api/cards/${id}`),
  queue: () => get<ReviewQueue>('/api/review/queue'),
  grade: (cardId: string, grade: Grade) => post<{ card: CardView; requeue: boolean }>('/api/review', { cardId, grade }),

  draft: (songId: string, lineIds: string[]) => post<DraftResult>('/api/draft', { songId, lineIds }),
  tutor: () => get<TutorSongGroup[]>('/api/tutor'),
  tutorMarkdownUrl: '/api/tutor/export.md',
  addQuestion: (q: { lineId?: string; wordId?: string; text: string }) => post<Question>('/api/questions', q),
  updateQuestion: (id: string, p: { text?: string; resolved?: boolean }) => patch<Question>(`/api/questions/${id}`, p),
  deleteQuestion: (id: string) => del(`/api/questions/${id}`),

  saveRecording: (lineId: string, blob: Blob) =>
    request<Recording>('PUT', `/api/lines/${lineId}/recording`, undefined, { blob }),
  deleteRecording: (lineId: string) => del(`/api/lines/${lineId}/recording`),
  recordingUrl: (rec: Recording) => `/api/lines/${rec.lineId}/recording?v=${encodeURIComponent(rec.createdAt)}`,

  importBackup: (data: unknown) => post<Record<string, number>>('/api/backup/import', data),
}

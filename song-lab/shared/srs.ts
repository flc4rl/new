import type { Grade } from './types'

// SM-2 with short learning steps, in the style Anki made familiar.
// A card is "learning" while reps is 0: "again" and "hard" bring it back within minutes,
// so it returns in the same session. "good" or "easy" graduate it to day-long intervals.

export interface SrsState {
  interval: number
  ease: number
  reps: number
  lapses: number
}

export interface SrsResult extends SrsState {
  due: Date
  /** True when the card should come back later in the same session. */
  requeue: boolean
}

export const START_EASE = 2.5
const MIN_EASE = 1.3
const AGAIN_MINUTES = 1
const HARD_MINUTES = 6
const GRADUATE_DAYS = 1
const EASY_DAYS = 4

export function startOfLocalDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

function inMinutes(now: Date, minutes: number): Date {
  return new Date(now.getTime() + minutes * 60_000)
}

/** Day intervals fall due at the start of the target day, so "due today" means any time today. */
function inDays(now: Date, days: number): Date {
  const d = startOfLocalDay(now)
  d.setDate(d.getDate() + days)
  return d
}

export function schedule(state: SrsState, grade: Grade, now: Date): SrsResult {
  let { interval, ease, reps, lapses } = state

  if (reps === 0) {
    // New card, or one being relearned after a lapse.
    if (grade === 'again') return { interval: 0, ease, reps, lapses, due: inMinutes(now, AGAIN_MINUTES), requeue: true }
    if (grade === 'hard') return { interval: 0, ease, reps, lapses, due: inMinutes(now, HARD_MINUTES), requeue: true }
    if (grade === 'easy') {
      ease += 0.15
      interval = EASY_DAYS
    } else {
      interval = GRADUATE_DAYS
    }
    return { interval, ease, reps: 1, lapses, due: inDays(now, interval), requeue: false }
  }

  if (grade === 'again') {
    return {
      interval: 0,
      ease: Math.max(MIN_EASE, ease - 0.2),
      reps: 0,
      lapses: lapses + 1,
      due: inMinutes(now, AGAIN_MINUTES),
      requeue: true,
    }
  }

  let next: number
  if (grade === 'hard') {
    next = interval * 1.2
    ease = Math.max(MIN_EASE, ease - 0.15)
  } else if (grade === 'good') {
    next = interval * ease
  } else {
    next = interval * ease * 1.3
    ease += 0.15
  }
  // Always move forward by at least a day, so a card never stalls.
  interval = Math.max(interval + 1, Math.round(next))
  return { interval, ease, reps: reps + 1, lapses, due: inDays(now, interval), requeue: false }
}

/** Short label for a grade button: when the card would come back. */
export function previewInterval(state: SrsState, grade: Grade, now: Date): string {
  const r = schedule(state, grade, now)
  if (r.requeue) {
    const minutes = Math.round((r.due.getTime() - now.getTime()) / 60_000)
    return `${minutes} min`
  }
  if (r.interval < 30) return `${r.interval} d`
  if (r.interval < 365) return `${Math.round(r.interval / 30)} mo`
  return `${(r.interval / 365).toFixed(1)} y`
}

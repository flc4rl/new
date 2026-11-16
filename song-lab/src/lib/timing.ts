import type { Line } from '../../shared/types'

/** Each press of a nudge button moves a line's start or end by this much. */
export const NUDGE_SEC = 0.25
/** The highlight moves on this much before a line's start, so it never trails the voice. */
const LOOKAHEAD_SEC = 0.1
/** How long an untimed ending is assumed to last. */
const FALLBACK_LENGTH_SEC = 6
const MIN_LENGTH_SEC = 0.1

export const round2 = (n: number) => Math.round(n * 100) / 100

/** The line being sung at time t: the last line that has started. */
export function activeLineAt(lines: Line[], t: number): Line | null {
  let best: Line | null = null
  for (const line of lines) {
    if (line.startSec == null || line.startSec > t + LOOKAHEAD_SEC) continue
    if (!best || line.startSec >= best.startSec!) best = line
  }
  return best
}

/** Where a line ends: its own end, or else where the next timed line begins. */
export function lineEnd(lines: Line[], line: Line): number | null {
  if (line.startSec == null) return null
  if (line.endSec != null && line.endSec > line.startSec) return line.endSec
  const index = lines.findIndex((l) => l.id === line.id)
  for (const later of lines.slice(index + 1)) {
    if (later.startSec != null && later.startSec > line.startSec) return later.startSec
  }
  return line.startSec + FALLBACK_LENGTH_SEC
}

export interface TimingChange {
  id: string
  startSec?: number | null
  endSec?: number | null
}

/**
 * Move a line's start or end. A line ends where the next begins, so moving a
 * start takes the previous line's end with it when the two were joined.
 */
export function nudge(lines: Line[], line: Line, edge: 'start' | 'end', delta: number): TimingChange[] {
  if (line.startSec == null) return []
  const end = lineEnd(lines, line)!
  if (edge === 'end') {
    const next = round2(Math.max(line.startSec + MIN_LENGTH_SEC, end + delta))
    return next === line.endSec ? [] : [{ id: line.id, endSec: next }]
  }
  const start = round2(Math.min(Math.max(0, line.startSec + delta), end - MIN_LENGTH_SEC))
  if (start === line.startSec) return []
  const changes: TimingChange[] = [{ id: line.id, startSec: start }]
  const previous = lines[lines.findIndex((l) => l.id === line.id) - 1]
  if (previous?.endSec != null && Math.abs(previous.endSec - line.startSec) < 0.01) {
    changes.push({ id: previous.id, endSec: start })
  }
  return changes
}

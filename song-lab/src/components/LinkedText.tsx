import { Fragment } from 'react'
import type { Segment } from '../lib/links'

/** Text whose words light up together with their counterparts in the other rows. */
export function LinkedText({
  segments,
  hover,
  onHover,
  tips,
}: {
  segments: Segment[]
  hover: string | null
  onHover: (wordId: string | null) => void
  /** A short note shown above a word when the pointer is on it. */
  tips?: Map<string, string>
}) {
  return (
    <>
      {segments.map((s, i) =>
        s.wordId ? (
          <span
            key={i}
            className={`link${hover === s.wordId ? ' is-linked' : ''}`}
            data-tip={tips?.get(s.wordId) || undefined}
            onMouseEnter={() => onHover(s.wordId)}
            onMouseLeave={() => onHover(null)}
          >
            {s.text}
          </span>
        ) : (
          <Fragment key={i}>{s.text}</Fragment>
        ),
      )}
    </>
  )
}

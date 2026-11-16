import { useEffect, useRef, type ReactNode, type RefObject } from 'react'
import { ExternalLink, Pause, Play } from 'lucide-react'
import type { PlayerSnapshot, SongPlayer } from '../lib/player'
import { Button } from './ui'
import { formatTime } from '../../shared/text'
import { watchUrl } from '../../shared/youtube'

/** The three speeds a learner reaches for most; any others the video allows are shown smaller. */
const MAIN_RATES = [0.5, 0.75, 1]

function formatRate(rate: number): string {
  return `${Number(rate.toFixed(2))}×`
}

export function Speeds({
  snap,
  onChange,
}: {
  snap: PlayerSnapshot
  onChange: (rate: number) => void
}) {
  const known = snap.rates.length > 0
  const rates = known ? snap.rates : MAIN_RATES
  return (
    <div className="speeds" role="group" aria-label="Playback speed">
      {rates.map((rate) => (
        <button
          key={rate}
          type="button"
          className={`speed${MAIN_RATES.includes(rate) ? ' speed-main' : ''}`}
          aria-pressed={snap.rate === rate}
          disabled={!known || snap.status !== 'ready'}
          onClick={() => onChange(rate)}
        >
          {formatRate(rate)}
        </button>
      ))}
    </div>
  )
}

/** Shows the playing position. Updated outside React so the page does not re-render ten times a second. */
export function TimeReadout({ player, duration }: { player: SongPlayer | null; duration: number }) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!player) return
    return player.onTick((t) => {
      if (ref.current) ref.current.textContent = formatTime(t)
    })
  }, [player])
  return (
    <span className="transport-time">
      <b ref={ref}>0:00.0</b> / {formatTime(duration, 0)}
    </span>
  )
}

export function PlayerFrame({
  hostRef,
  snap,
  youtubeId,
  onRetry,
}: {
  hostRef: RefObject<HTMLDivElement | null>
  snap: PlayerSnapshot
  youtubeId: string
  onRetry: () => void
}) {
  const error = snap.status === 'error' ? snap.error : null
  return (
    <div className="player-frame">
      <div ref={hostRef} className="player-host" />
      {snap.status === 'loading' && (
        <div className="player-cover">
          <p>Loading the player…</p>
        </div>
      )}
      {error && (
        <div className="player-cover" role="alert">
          <h3>{error.embedBlocked ? 'This video cannot play inside the app' : 'The video did not load'}</h3>
          <p>{error.message}</p>
          <div className="player-cover-actions">
            <a className="btn btn-primary btn-md" href={watchUrl(youtubeId)} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
              <span>Open on YouTube</span>
            </a>
            {!error.embedBlocked && <Button onClick={onRetry}>Try again</Button>}
          </div>
        </div>
      )}
    </div>
  )
}

export function Transport({
  player,
  snap,
  onRate,
  children,
}: {
  player: SongPlayer | null
  snap: PlayerSnapshot
  onRate: (rate: number) => void
  children?: ReactNode
}) {
  const ready = snap.status === 'ready'
  const active = snap.playing || snap.buffering
  return (
    <div className="transport">
      <Button
        variant="primary"
        icon={active ? <Pause size={16} /> : <Play size={16} />}
        disabled={!ready}
        onClick={() => player?.toggle()}
        title="Play or pause (Space)"
      >
        {active ? 'Pause' : 'Play'}
      </Button>
      <TimeReadout player={player} duration={snap.duration} />
      {children}
      <Speeds snap={snap} onChange={onRate} />
    </div>
  )
}

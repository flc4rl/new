import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { Circle, Headphones, Play, Square, Trash2, Volume2 } from 'lucide-react'
import { api } from '../api'
import { Button, useToast } from './ui'
import { describeMicError, MAX_TAKE_MS, recordingSupported, startTake, type Take } from '../lib/recorder'
import type { Line, Recording } from '../../shared/types'

type Phase = 'idle' | 'starting' | 'recording' | 'saving' | 'original' | 'take'

export interface ShadowControls {
  toggleRecord: () => void
  compare: () => void
}

export function ShadowPanel({
  line,
  recording,
  canPlayLine,
  controls,
  onLoopForTake,
  onStopLoop,
  onPlayLine,
  onSaved,
  onDeleted,
}: {
  /** The highlighted line; takes belong to it. */
  line: Line | null
  recording: Recording | null
  /** False until the player is ready and the line has a start. */
  canPlayLine: boolean
  controls: Ref<ShadowControls>
  /** Loop the line until told to stop, for shadowing along. */
  onLoopForTake: (line: Line) => void
  onStopLoop: () => void
  /** Play the line once, then call back. */
  onPlayLine: (line: Line, onFinish: () => void) => void
  onSaved: (recording: Recording) => void
  onDeleted: (lineId: string) => void
}) {
  const toast = useToast()
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const take = useRef<Take | null>(null)
  const takeLine = useRef<Line | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const supported = recordingSupported()

  const stopAudio = () => {
    audio.current?.pause()
    audio.current = null
  }

  // Leaving the page stops everything.
  useEffect(
    () => () => {
      take.current?.cancel()
      stopAudio()
      window.clearInterval(timer.current)
    },
    [],
  )

  // Moving to another line ends whatever was playing for the last one, but never a take in progress.
  useEffect(() => {
    if (phase === 'take' || phase === 'original') {
      stopAudio()
      setPhase('idle')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line?.id])

  async function start() {
    if (!line || phase === 'starting' || phase === 'recording') return
    setError(null)
    stopAudio()
    setPhase('starting')
    try {
      take.current = await startTake()
    } catch (err) {
      setPhase('idle')
      setError(describeMicError(err))
      return
    }
    takeLine.current = line
    setPhase('recording')
    setElapsed(0)
    const began = Date.now()
    window.clearInterval(timer.current)
    timer.current = window.setInterval(() => {
      const ms = Date.now() - began
      setElapsed(ms)
      if (ms >= MAX_TAKE_MS) void stop()
    }, 200)
    if (canPlayLine) onLoopForTake(line)
  }

  async function stop() {
    const current = take.current
    const target = takeLine.current
    if (!current || !target) return
    take.current = null
    window.clearInterval(timer.current)
    onStopLoop()
    setPhase('saving')
    try {
      const blob = await current.stop()
      if (blob.size === 0) throw new Error('Nothing was recorded. Check the microphone and try again.')
      onSaved(await api.saveRecording(target.id, blob))
      setPhase('idle')
    } catch (err) {
      setPhase('idle')
      toast.error(err)
    }
  }

  function playTake(after?: () => void) {
    if (!recording) return
    stopAudio()
    const el = new Audio(api.recordingUrl(recording))
    audio.current = el
    setPhase('take')
    el.onended = () => {
      if (audio.current === el) {
        audio.current = null
        setPhase('idle')
        after?.()
      }
    }
    el.onerror = () => {
      if (audio.current === el) setPhase('idle')
      toast.error(new Error('The take could not be played.'))
    }
    void el.play().catch(() => setPhase('idle'))
  }

  /** The original line, then the take, back to back. */
  function compare() {
    if (!line || !recording || !canPlayLine) return
    stopAudio()
    setPhase('original')
    onPlayLine(line, () => playTake())
  }

  async function remove() {
    if (!line || !recording) return
    stopAudio()
    try {
      await api.deleteRecording(line.id)
      onDeleted(line.id)
    } catch (err) {
      toast.error(err)
    }
  }

  const recordingNow = phase === 'recording' || phase === 'starting'
  useImperativeHandle(controls, () => ({
    toggleRecord: () => void (recordingNow ? stop() : start()),
    compare,
  }))

  return (
    <section className="stage-section" aria-label="Shadowing">
      <h2>Shadowing</h2>
      {!supported ? (
        <p className="stage-note">This browser cannot record from the microphone. Try a current Chrome, Edge, Firefox or Safari.</p>
      ) : !line ? (
        <p className="stage-note">Pick a line to shadow: click its number, or move to it with the arrow keys.</p>
      ) : (
        <>
          <div className="stage-row">
            {recordingNow ? (
              <Button variant="danger" icon={<Square size={14} />} onClick={() => void stop()} title="Stop recording (R)">
                Stop · {(elapsed / 1000).toFixed(1)} s
              </Button>
            ) : (
              <Button
                icon={<Circle size={14} className="rec-dot" />}
                onClick={() => void start()}
                disabled={phase === 'saving'}
                title="Record yourself while the line loops (R)"
              >
                {phase === 'saving' ? 'Saving…' : recording ? 'Record again' : 'Record'}
              </Button>
            )}
            <Button
              icon={<Volume2 size={15} />}
              disabled={!recording || !canPlayLine || recordingNow || phase === 'saving'}
              pressed={phase === 'original' || phase === 'take'}
              onClick={compare}
              title="The original line, then your take (C)"
            >
              {phase === 'original' ? 'Original…' : phase === 'take' ? 'Your take…' : 'Original, then take'}
            </Button>
            {recording && !recordingNow && (
              <>
                <Button variant="ghost" size="sm" icon={<Play size={14} />} aria-label="Play your take" title="Play your take only" onClick={() => playTake()} />
                <Button variant="ghost" size="sm" icon={<Trash2 size={14} />} aria-label="Delete your take" title="Delete your take" onClick={() => void remove()} />
              </>
            )}
          </div>
          <p className="stage-note">
            {recordingNow ? (
              <span className="rec-live">Recording line {line.order + 1}. Speak along with the singer.</span>
            ) : recording ? (
              <>Your latest take of line {line.order + 1} is kept. Recording again replaces it.</>
            ) : (
              <>
                <Headphones size={13} className="inline-icon" /> Wear headphones, so the take holds only your voice.
              </>
            )}
          </p>
          {error && (
            <p className="stage-note stage-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  )
}

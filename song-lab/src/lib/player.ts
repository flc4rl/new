// A thin engine around the YouTube IFrame Player API.
//
// The API has no time-update event and reports the current time in coarse
// steps, so the engine polls about ten times a second and keeps its own clock
// between reports. That clock drives the line highlight, tap-to-sync and loops.

interface YTPlayer {
  playVideo(): void
  pauseVideo(): void
  seekTo(seconds: number, allowSeekAhead: boolean): void
  getCurrentTime(): number
  getDuration(): number
  getPlayerState(): number
  getPlaybackRate(): number
  setPlaybackRate(rate: number): void
  getAvailablePlaybackRates(): number[]
  loadVideoById(options: { videoId: string; startSeconds?: number }): void
  getIframe(): HTMLIFrameElement
  destroy(): void
}

interface YTNamespace {
  Player: new (
    host: HTMLElement,
    options: {
      videoId: string
      width?: string
      height?: string
      playerVars?: Record<string, string | number>
      events?: {
        onReady?: () => void
        onStateChange?: (e: { data: number }) => void
        onPlaybackRateChange?: (e: { data: number }) => void
        onError?: (e: { data: number }) => void
      }
    },
  ) => YTPlayer
}

declare global {
  interface Window {
    YT?: YTNamespace
    onYouTubeIframeAPIReady?: () => void
  }
}

const YT_ENDED = 0
const YT_PLAYING = 1
const YT_BUFFERING = 3

let apiPromise: Promise<YTNamespace> | null = null

function loadApi(): Promise<YTNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (!apiPromise) {
    apiPromise = new Promise<YTNamespace>((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => {
        previous?.()
        resolve(window.YT!)
      }
      const script = document.createElement('script')
      script.src = 'https://www.youtube.com/iframe_api'
      script.async = true
      script.onerror = () => {
        apiPromise = null
        script.remove()
        reject(new Error('offline'))
      }
      document.head.appendChild(script)
    })
  }
  return apiPromise
}

export interface PlayerError {
  code: number
  /** True when the video's owner has switched off playback inside other sites. */
  embedBlocked: boolean
  message: string
}

function describeError(code: number): PlayerError {
  switch (code) {
    case 101:
    case 150:
      return {
        code,
        embedBlocked: true,
        message: 'The owner of this video does not allow it to play inside other apps, so it cannot be used here.',
      }
    case 100:
      return { code, embedBlocked: false, message: 'This video is not available. It may have been removed or made private.' }
    case 2:
      return { code, embedBlocked: false, message: 'YouTube does not recognise this video link.' }
    case 153:
      return {
        code,
        embedBlocked: false,
        message: 'YouTube refused to play the video here. Open the app at http://localhost, not from a file.',
      }
    case 0:
      return { code, embedBlocked: false, message: 'YouTube could not be reached. Check the internet connection.' }
    default:
      return { code, embedBlocked: false, message: 'The video could not be played in this browser.' }
  }
}

export interface LoopSpec {
  start: number
  end: number
  /** How many times to play the span. Infinity loops until stopped. */
  repeats: number
  /** Seconds of silence between repeats. */
  pauseSec: number
  /** Seconds to start before the span, so the first syllable is not clipped. */
  leadInSec: number
  /** Called after each pass with the number of passes done. */
  onPass?: (done: number) => void
  /** Called once after the last pass. */
  onFinish?: () => void
}

export interface PlayerSnapshot {
  status: 'loading' | 'ready' | 'error'
  error: PlayerError | null
  playing: boolean
  buffering: boolean
  rate: number
  /** The playback rates YouTube offers for the loaded video. */
  rates: number[]
  duration: number
  /** Present while a loop or clip is running. */
  loop: { done: number; repeats: number; waiting: boolean } | null
}

interface ActiveLoop extends LoopSpec {
  done: number
  waiting: boolean
  pauseTimer?: number
  endTimer?: number
}

const POLL_MS = 100
/** After a seek, trust the target time until YouTube's own report catches up. */
const SEEK_SETTLE_MS = 1500

export class SongPlayer {
  private yt: YTPlayer | null = null
  private destroyed = false
  private snapshot: PlayerSnapshot = {
    status: 'loading',
    error: null,
    playing: false,
    buffering: false,
    rate: 1,
    rates: [],
    duration: 0,
    loop: null,
  }
  private listeners = new Set<() => void>()
  private tickers = new Set<(time: number) => void>()
  private poll: number | undefined
  private wantedRate: number
  private reported = 0
  private reportedAt = 0
  private pendingSeek: { target: number; at: number } | null = null
  private loop: ActiveLoop | null = null
  private videoId: string

  constructor(host: HTMLElement, videoId: string, initialRate = 1) {
    this.videoId = videoId
    this.wantedRate = initialRate
    this.snapshot = { ...this.snapshot, rate: initialRate }
    loadApi()
      .then((YT) => {
        if (this.destroyed) return
        // YouTube swaps the element it is given for an iframe, so hand it a child React does not own.
        const slot = document.createElement('div')
        host.replaceChildren(slot)
        this.yt = new YT.Player(slot, {
          videoId,
          width: '100%',
          height: '100%',
          playerVars: {
            playsinline: 1,
            rel: 0,
            iv_load_policy: 3,
            enablejsapi: 1,
            origin: window.location.origin,
          },
          events: {
            onReady: () => this.handleReady(),
            onStateChange: (e) => this.handleState(e.data),
            onPlaybackRateChange: (e) => this.handleRate(e.data),
            onError: (e) => this.fail(e.data),
          },
        })
      })
      .catch(() => this.fail(0))
  }

  // ---- subscriptions ----

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): PlayerSnapshot => this.snapshot

  /** Called about ten times a second with the current time while the player exists. */
  onTick(fn: (time: number) => void): () => void {
    this.tickers.add(fn)
    return () => this.tickers.delete(fn)
  }

  private set(patch: Partial<PlayerSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch }
    this.listeners.forEach((listener) => listener())
  }

  // ---- YouTube events ----

  private handleReady(): void {
    if (this.destroyed || !this.yt) return
    this.refreshRates()
    this.yt.setPlaybackRate(this.wantedRate)
    this.set({ status: 'ready', error: null, rate: this.wantedRate, duration: this.yt.getDuration() || 0 })
    this.poll = window.setInterval(() => this.tick(), POLL_MS)
  }

  private refreshRates(): void {
    const rates = this.yt?.getAvailablePlaybackRates?.() ?? []
    const sorted = [...rates].sort((a, b) => a - b)
    if (sorted.length && sorted.join() !== this.snapshot.rates.join()) {
      if (!sorted.includes(this.wantedRate)) this.wantedRate = sorted.includes(1) ? 1 : sorted[0]
      this.set({ rates: sorted })
    }
  }

  private handleState(state: number): void {
    if (this.destroyed || !this.yt) return
    const playing = state === YT_PLAYING
    if (playing) {
      this.refreshRates()
      // A rate set before playback starts does not always stick.
      if (this.yt.getPlaybackRate() !== this.wantedRate) this.yt.setPlaybackRate(this.wantedRate)
      this.reported = this.yt.getCurrentTime()
      this.reportedAt = performance.now()
    }
    this.set({
      playing,
      buffering: state === YT_BUFFERING,
      duration: this.yt.getDuration() || this.snapshot.duration,
      error: null,
      status: 'ready',
    })
    if (state === YT_ENDED && this.loop && !this.loop.waiting) this.passEnd()
    this.returnFocus()
  }

  private handleRate(rate: number): void {
    this.wantedRate = rate
    this.set({ rate })
  }

  private fail(code: number): void {
    if (this.destroyed) return
    this.clearLoop()
    this.set({ status: 'error', error: describeError(code), playing: false, buffering: false, loop: null })
  }

  /**
   * A click inside the video moves keyboard focus into YouTube's frame, where
   * the app's shortcuts cannot be heard. Hand focus back to the page.
   */
  private returnFocus(): void {
    const frame = this.yt?.getIframe?.()
    if (frame && document.activeElement === frame) {
      frame.blur()
      window.focus()
    }
  }

  // ---- clock ----

  /** The current position in seconds, interpolated between YouTube's reports. */
  time(): number {
    if (!this.yt || this.snapshot.status !== 'ready') return 0
    const nowMs = performance.now()
    const raw = this.yt.getCurrentTime() ?? 0
    if (this.pendingSeek) {
      const settled = Math.abs(raw - this.pendingSeek.target) < 0.75
      if (settled || nowMs - this.pendingSeek.at > SEEK_SETTLE_MS) {
        this.pendingSeek = null
        this.reported = raw
        this.reportedAt = nowMs
      } else {
        const elapsed = this.snapshot.playing ? ((nowMs - this.pendingSeek.at) / 1000) * this.snapshot.rate : 0
        return this.pendingSeek.target + elapsed
      }
    }
    if (raw !== this.reported) {
      this.reported = raw
      this.reportedAt = nowMs
    }
    if (!this.snapshot.playing) return raw
    const sinceReport = Math.min((nowMs - this.reportedAt) / 1000, 0.5)
    return this.reported + sinceReport * this.snapshot.rate
  }

  private tick(): void {
    if (this.destroyed || !this.yt) return
    const t = this.time()
    const loop = this.loop
    if (loop && !loop.waiting && this.snapshot.playing) {
      const farOutside = t > loop.end + 2 || t < loop.start - loop.leadInSec - 2
      if (farOutside && !this.pendingSeek) {
        // The user moved somewhere else with YouTube's own controls: let them.
        this.stopLoop()
      } else {
        const remainingMs = ((loop.end - t) / this.snapshot.rate) * 1000
        if (remainingMs <= 0) this.passEnd()
        else if (remainingMs < POLL_MS * 1.5 && loop.endTimer === undefined) {
          // The end falls between two polls: stop on time, not on the next poll.
          loop.endTimer = window.setTimeout(() => this.passEnd(), remainingMs)
        }
      }
    }
    this.tickers.forEach((fn) => fn(t))
  }

  // ---- transport ----

  play(): void {
    this.yt?.playVideo()
  }

  pause(): void {
    this.yt?.pauseVideo()
  }

  toggle(): void {
    if (this.snapshot.playing || this.snapshot.buffering) this.pause()
    else this.play()
  }

  seek(seconds: number): void {
    if (!this.yt) return
    const target = Math.max(0, seconds)
    this.pendingSeek = { target, at: performance.now() }
    this.yt.seekTo(target, true)
  }

  setRate(rate: number): void {
    if (!this.yt) return
    this.wantedRate = rate
    this.yt.setPlaybackRate(rate)
    this.set({ rate })
  }

  /** Move to the next slower (-1) or faster (+1) rate YouTube offers. */
  stepRate(direction: -1 | 1): number {
    const { rates, rate } = this.snapshot
    const index = rates.indexOf(rate)
    const next = rates[index + direction]
    if (index >= 0 && next !== undefined) this.setRate(next)
    return this.snapshot.rate
  }

  /** Switch to another video, for review cards drawn from different songs. */
  load(videoId: string, rate: number, startSec = 0): void {
    if (!this.yt) return
    // The first video may have failed before the player was ready; the clock still has to run.
    if (this.poll === undefined) this.poll = window.setInterval(() => this.tick(), POLL_MS)
    this.clearLoop()
    this.wantedRate = rate
    if (videoId !== this.videoId) {
      this.videoId = videoId
      this.pendingSeek = { target: startSec, at: performance.now() }
      // A new video starts with a clean slate, whatever happened to the last one.
      this.set({ loop: null, rate, status: 'ready', error: null })
      this.yt.loadVideoById({ videoId, startSeconds: startSec })
    } else {
      this.set({ loop: null, rate })
      if (this.yt.getPlaybackRate() !== rate) this.yt.setPlaybackRate(rate)
    }
  }

  get currentVideoId(): string {
    return this.videoId
  }

  // ---- loops and clips ----

  /** Play a span of the video a number of times, with a pause between repeats. */
  startLoop(spec: LoopSpec): void {
    if (!this.yt) return
    this.clearLoop()
    this.loop = { ...spec, done: 0, waiting: false }
    this.publishLoop()
    this.seek(spec.start - spec.leadInSec)
    this.play()
  }

  /** Play a span once. */
  playClip(start: number, end: number, leadInSec: number, onFinish?: () => void): void {
    this.startLoop({ start, end, repeats: 1, pauseSec: 0, leadInSec, onFinish })
  }

  stopLoop(): void {
    if (!this.loop) return
    this.clearLoop()
    this.set({ loop: null })
  }

  private clearLoop(): void {
    if (!this.loop) return
    window.clearTimeout(this.loop.pauseTimer)
    window.clearTimeout(this.loop.endTimer)
    this.loop = null
  }

  private publishLoop(): void {
    const loop = this.loop
    this.set({ loop: loop ? { done: loop.done, repeats: loop.repeats, waiting: loop.waiting } : null })
  }

  private passEnd(): void {
    const loop = this.loop
    if (!loop || loop.waiting) return
    window.clearTimeout(loop.endTimer)
    loop.endTimer = undefined
    this.pause()
    loop.done += 1
    loop.onPass?.(loop.done)
    if (loop.done >= loop.repeats) {
      this.loop = null
      this.set({ loop: null })
      loop.onFinish?.()
      return
    }
    loop.waiting = true
    this.publishLoop()
    loop.pauseTimer = window.setTimeout(() => {
      if (this.loop !== loop) return
      loop.waiting = false
      this.publishLoop()
      this.seek(loop.start - loop.leadInSec)
      this.play()
    }, loop.pauseSec * 1000)
  }

  destroy(): void {
    this.destroyed = true
    window.clearInterval(this.poll)
    this.clearLoop()
    this.listeners.clear()
    this.tickers.clear()
    try {
      this.yt?.destroy()
    } catch {
      // The frame is already gone.
    }
    this.yt = null
  }
}

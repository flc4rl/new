// Recording from the microphone with MediaRecorder.

const TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']

/** Long enough for any line; stops a take that was forgotten. */
export const MAX_TAKE_MS = 60_000

export function recordingSupported(): boolean {
  return typeof window !== 'undefined' && 'MediaRecorder' in window && !!navigator.mediaDevices?.getUserMedia
}

function pickType(): string | undefined {
  return TYPES.find((t) => MediaRecorder.isTypeSupported(t))
}

export function describeMicError(err: unknown): string {
  const name = err instanceof DOMException ? err.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'The browser was not allowed to use the microphone. Allow it in the site settings (the icon left of the address), then try again.'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No microphone was found. Plug one in, then try again.'
  if (name === 'NotReadableError') return 'The microphone is busy in another app. Close it there, then try again.'
  return 'The microphone could not be started.'
}

export interface Take {
  stop: () => Promise<Blob>
  cancel: () => void
}

/** Ask for the microphone and start recording. Resolves once recording has begun. */
export async function startTake(): Promise<Take> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true },
  })
  const type = pickType()
  const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
  const chunks: Blob[] = []
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data)
  }
  const release = () => stream.getTracks().forEach((t) => t.stop())
  const stopped = new Promise<Blob>((resolve) => {
    recorder.onstop = () => {
      release()
      resolve(new Blob(chunks, { type: recorder.mimeType || type || 'audio/webm' }))
    }
  })
  recorder.start(250)
  return {
    stop: () => {
      if (recorder.state !== 'inactive') recorder.stop()
      return stopped
    },
    cancel: () => {
      recorder.ondataavailable = null
      if (recorder.state !== 'inactive') recorder.stop()
      release()
    },
  }
}

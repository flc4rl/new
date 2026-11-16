const ID = /^[\w-]{11}$/

/** Pull the 11-character video id out of any common YouTube link, or a bare id. */
export function parseYouTubeId(input: string): string | null {
  const s = input.trim()
  if (!s) return null
  if (ID.test(s)) return s
  let url: URL
  try {
    url = new URL(/^[a-z]+:\/\//i.test(s) ? s : `https://${s}`)
  } catch {
    return null
  }
  const host = url.hostname.toLowerCase().replace(/^(www\.|m\.|music\.)/, '')
  let id: string | null = null
  if (host === 'youtu.be') {
    id = url.pathname.split('/')[1] ?? null
  } else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    if (url.pathname === '/watch') id = url.searchParams.get('v')
    else id = url.pathname.match(/^\/(?:embed|shorts|live|v)\/([^/?#]+)/)?.[1] ?? null
  }
  return id && ID.test(id) ? id : null
}

export function watchUrl(youtubeId: string, startSec?: number | null): string {
  const t = startSec && startSec > 0 ? `&t=${Math.floor(startSec)}s` : ''
  return `https://www.youtube.com/watch?v=${youtubeId}${t}`
}

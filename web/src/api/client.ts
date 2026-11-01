// Typed client for the Hearsay server API (/api, design.md §10).

class ApiError extends Error {
  override readonly name = 'ApiError'
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(
  method: string,
  route: string,
  body?: unknown,
  raw?: { body: BodyInit; type: string },
): Promise<T> {
  const init: RequestInit = { method }
  if (raw) {
    init.headers = { 'Content-Type': raw.type }
    init.body = raw.body
  } else if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  const res = await fetch(`/api${route}`, init)
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const data = (await res.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') message = data.detail
    } catch {
      // Not JSON (a proxy's error page): keep the status line.
    }
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface Health {
  status: 'ok'
  version: string
  schema: number
}

export type Policy = 'auto' | 'manual'
type TaskStatus = 'pending' | 'running' | 'done' | 'error'

/** A show in the Library list. */
export interface FeedSummary {
  id: number
  url: string
  title: string | null
  author: string | null
  artwork: string | null
  policy: Policy
  error: string | null
  episode_count: number
  unplayed: number
  newest_title: string | null
  newest_published_at: string | null
}

export interface Feed {
  id: number
  url: string
  title: string | null
  author: string | null
  description: string | null
  link: string | null
  artwork: string | null
  policy: Policy
  backfill_n: number
  diarize: boolean
  error: string | null
  last_polled_at: string | null
}

export interface EpisodeSummary {
  id: number
  title: string | null
  published_at: string | null
  duration_s: number | null
  state: string
  artwork: string | null
  task: TaskStatus | null
  finished: boolean
}

export interface FeedDetail {
  feed: Feed
  episodes: EpisodeSummary[]
}

export interface FeedPreview {
  url: string
  title: string
  author: string | null
  image: string | null
  feed_id: number | null
  durations: (number | null)[]
}

export interface Subscribe {
  url: string
  policy: Policy
  backfill_n: number
}

export interface Imported {
  added: number
  skipped: number
}

export const api = {
  health: () => request<Health>('GET', '/health'),
  feeds: () => request<FeedSummary[]>('GET', '/feeds'),
  feed: (id: number) => request<FeedDetail>('GET', `/feeds/${id}`),
  previewFeed: (url: string) => request<FeedPreview>('POST', '/feeds/preview', { url }),
  subscribe: (body: Subscribe) => request<Feed>('POST', '/feeds', body),
  updateFeed: (id: number, body: { policy?: Policy; diarize?: boolean }) =>
    request<Feed>('PATCH', `/feeds/${id}`, body),
  unsubscribe: (id: number) => request<undefined>('DELETE', `/feeds/${id}`),
  refreshFeed: (id: number) => request<Feed>('POST', `/feeds/${id}/refresh`),
  importOpml: (file: Blob) =>
    request<Imported>('POST', '/feeds/import-opml', undefined, {
      body: file,
      type: 'text/x-opml',
    }),
  process: (episodeId: number, now = false) =>
    request<undefined>('POST', `/episodes/${episodeId}/process`, { now }),
}

export const OPML_EXPORT_URL = '/api/feeds/export-opml'

/** A cached artwork file (`size` px on its longer side). */
export function imageUrl(sha: string, size: 128 | 512): string {
  return `/api/images/${sha}-${size}.webp`
}

import type { EpisodeSummary, FeedDetail, FeedSummary } from '@/api/client'

export const GRIDLOCK: FeedSummary = {
  id: 1,
  url: 'https://pods.example/gridlock.xml',
  title: 'Gridlock',
  author: 'Dana',
  artwork: 'a'.repeat(64),
  policy: 'auto',
  error: null,
  episode_count: 3,
  unplayed: 2,
  newest_title: 'The battery problem nobody budgets for',
  newest_published_at: '2026-10-28T14:00:00+00:00',
}

function episode(id: number, changes: Partial<EpisodeSummary> = {}): EpisodeSummary {
  return {
    id,
    title: `Episode ${id}`,
    published_at: '2026-10-28T14:00:00+00:00',
    duration_s: 4320,
    state: 'discovered',
    artwork: null,
    task: null,
    finished: false,
    ...changes,
  }
}

export function gridlockDetail(changes: Partial<FeedDetail['feed']> = {}): FeedDetail {
  return {
    feed: {
      id: 1,
      url: GRIDLOCK.url,
      title: 'Gridlock',
      author: 'Dana',
      description: null,
      link: null,
      artwork: GRIDLOCK.artwork,
      policy: 'auto',
      backfill_n: 10,
      diarize: true,
      error: null,
      last_polled_at: '2026-11-01T10:00:00+00:00',
      ...changes,
    },
    episodes: [
      episode(3, { title: 'The battery problem nobody budgets for', task: 'pending' }),
      episode(2, { state: 'downloaded', finished: true }),
      episode(1),
    ],
  }
}

// Every server read the app makes, with its query key. Components use these rather than
// writing keys by hand, so invalidation always matches.

import { queryOptions } from '@tanstack/react-query'

import { api } from './client'

export const queries = {
  health: () =>
    queryOptions({ queryKey: ['health'], queryFn: api.health, refetchInterval: 60_000 }),
  feeds: () => queryOptions({ queryKey: ['feeds'], queryFn: api.feeds }),
  feed: (id: number) =>
    queryOptions({
      queryKey: ['feeds', id],
      queryFn: () => api.feed(id),
      // Until live events arrive (M2.4), follow downloads by asking again while any are going.
      refetchInterval: (query) =>
        query.state.data?.episodes.some((e) => e.task === 'pending' || e.task === 'running')
          ? 5_000
          : false,
    }),
}

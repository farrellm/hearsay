// Every server read the app makes, with its query key. Components use these rather than
// writing keys by hand, so invalidation always matches.

import { queryOptions } from '@tanstack/react-query'

import { api } from './client'

export const queries = {
  health: () =>
    queryOptions({ queryKey: ['health'], queryFn: api.health, refetchInterval: 60_000 }),
}

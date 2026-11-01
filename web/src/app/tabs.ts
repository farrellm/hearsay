export const TABS = ['Library', 'Search', 'Books', 'Up Next'] as const
export type Tab = (typeof TABS)[number]

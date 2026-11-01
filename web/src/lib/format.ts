// How times, dates and lengths read in the interface (docs/ui.md).

/** "1 h 12 min", "48 min", "under a minute". */
export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 1) return 'under a minute'
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `${m} min`
  return m === 0 ? `${h} h` : `${h} h ${m} min`
}

/** "Oct 28" this year, "Oct 28, 2024" before. */
export function formatDate(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  const sameYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

/** "9 h", "40 min": an amount of time to the hour once it's over an hour and a half. */
export function formatRough(seconds: number): string {
  const hours = seconds / 3600
  if (hours >= 1.5) return `${Math.round(hours)} h`
  return formatDuration(seconds)
}

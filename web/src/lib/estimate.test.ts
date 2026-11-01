import { describe, expect, it } from 'vitest'

import { estimate } from './estimate'
import { formatDate, formatDuration, formatRough } from './format'

describe('estimate', () => {
  it('adds up the known lengths and counts the rest', () => {
    expect(estimate([3600, null, 1800])).toEqual({
      audioSeconds: 5400,
      gpuSeconds: 450,
      unknown: 1,
    })
  })
})

describe('format', () => {
  it('reads lengths the way the interface says them', () => {
    expect(formatDuration(4320)).toBe('1 h 12 min')
    expect(formatDuration(2880)).toBe('48 min')
    expect(formatDuration(7200)).toBe('2 h')
    expect(formatDuration(20)).toBe('under a minute')
    expect(formatRough(9 * 3600 + 600)).toBe('9 h')
    expect(formatRough(2400)).toBe('40 min')
  })

  it('leaves the year off dates in this year', () => {
    const now = new Date('2026-11-01T12:00:00Z')
    expect(formatDate('2026-10-28T14:00:00+00:00', now)).toBe('Oct 28')
    expect(formatDate('2024-10-28T14:00:00+00:00', now)).toBe('Oct 28, 2024')
  })
})

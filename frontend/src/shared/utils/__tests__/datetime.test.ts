import { describe, it, expect } from 'vitest'
import { formatRelativeTime, formatDateTime } from '../datetime'

const NOW = new Date('2026-06-26T12:00:00Z').getTime()
const rel = (iso: string) => formatRelativeTime(iso, NOW, 'en')

describe('formatRelativeTime', () => {
  it('reads the exact instant as "now"', () => {
    expect(rel('2026-06-26T12:00:00Z')).toBe('now')
  })

  it('formats seconds in the past', () => {
    expect(rel('2026-06-26T11:59:30Z')).toBe('30 seconds ago')
  })

  it('formats minutes in the past', () => {
    expect(rel('2026-06-26T11:55:00Z')).toBe('5 minutes ago')
  })

  it('formats hours in the past', () => {
    expect(rel('2026-06-26T10:00:00Z')).toBe('2 hours ago')
  })

  it('uses "yesterday" for one day prior', () => {
    expect(rel('2026-06-25T12:00:00Z')).toBe('yesterday')
  })

  it('formats days in the future', () => {
    expect(rel('2026-06-29T12:00:00Z')).toBe('in 3 days')
  })

  it('falls back to an absolute string for an unparseable value', () => {
    expect(formatRelativeTime('not-a-date', NOW, 'en')).toBe(
      formatDateTime('not-a-date'),
    )
  })
})

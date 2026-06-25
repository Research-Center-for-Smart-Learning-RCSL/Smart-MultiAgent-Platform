import { describe, it, expect } from 'vitest'
import { relativeTime } from '../lib/relativeTime'

const NOW = new Date('2026-06-25T12:00:00Z')
const minus = (ms: number) => new Date(NOW.getTime() - ms).toISOString()

const SEC = 1000
const MIN = 60 * SEC
const HOUR = 60 * MIN
const DAY = 24 * HOUR

describe('relativeTime', () => {
  it('reports "just now" under a minute', () => {
    expect(relativeTime(minus(30 * SEC), NOW)).toEqual({ unit: 'justNow' })
  })

  it('reports minutes', () => {
    expect(relativeTime(minus(5 * MIN), NOW)).toEqual({ unit: 'minutes', n: 5 })
  })

  it('reports hours', () => {
    expect(relativeTime(minus(3 * HOUR), NOW)).toEqual({ unit: 'hours', n: 3 })
  })

  it('reports yesterday at the 1-day boundary', () => {
    expect(relativeTime(minus(DAY), NOW)).toEqual({ unit: 'yesterday' })
  })

  it('reports days for 2-6 days', () => {
    expect(relativeTime(minus(3 * DAY), NOW)).toEqual({ unit: 'days', n: 3 })
  })

  it('falls back to a locale date at 7+ days', () => {
    const r = relativeTime(minus(10 * DAY), NOW)
    expect(r.unit).toBe('date')
  })

  it('collapses future timestamps (clock skew) to "just now"', () => {
    expect(relativeTime(minus(-5 * MIN), NOW)).toEqual({ unit: 'justNow' })
  })

  it('returns the raw value for an unparseable timestamp', () => {
    expect(relativeTime('not-a-date', NOW)).toEqual({ unit: 'date', value: 'not-a-date' })
  })
})

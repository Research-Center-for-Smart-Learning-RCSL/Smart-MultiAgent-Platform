import { describe, expect, it } from 'vitest'
import { formatOffset, retryAfterSec } from '../lib/time.ts'

describe('retryAfterSec', () => {
  it('reads seconds, clamped to 1..60', () => {
    expect(retryAfterSec('7', 5)).toBe(7)
    expect(retryAfterSec('0', 5)).toBe(1)
    expect(retryAfterSec('900', 5)).toBe(60)
  })

  it('reads an HTTP date relative to now', () => {
    const now = Date.parse('2026-10-22T10:00:00Z')
    expect(retryAfterSec('Thu, 22 Oct 2026 10:00:12 GMT', 5, now)).toBe(12)
  })

  it('falls back when the header is missing or unreadable, never to NaN', () => {
    expect(retryAfterSec(null, 5)).toBe(5)
    expect(retryAfterSec(undefined, 10)).toBe(10)
    expect(retryAfterSec('soon', 5)).toBe(5)
  })
})

describe('formatOffset', () => {
  it('prints minutes and seconds', () => {
    expect(formatOffset(0)).toBe('00:00')
    expect(formatOffset(725.9)).toBe('12:05')
  })
})

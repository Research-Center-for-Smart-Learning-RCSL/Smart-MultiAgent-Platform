import { describe, expect, it } from 'vitest'
import { formatNumber, formatPercent } from '../number'

describe('number formatters', () => {
  it('uses locale formatting and switches to compact notation at ten thousand', () => {
    expect(formatNumber(9_999, 'en-US')).toBe('9,999')
    expect(formatNumber(10_000, 'en-US')).toBe('10K')
    expect(formatNumber(12_500, 'en-US')).toBe('12.5K')
    expect(formatNumber(1_234, 'de-DE')).toBe('1.234')
  })

  it('formats whole percentages and returns null for a zero denominator', () => {
    expect(formatPercent(1, 4, 'en-US')).toBe('25%')
    expect(formatPercent(1, 3, 'en-US')).toBe('33%')
    expect(formatPercent(1, 0, 'en-US')).toBeNull()
  })
})

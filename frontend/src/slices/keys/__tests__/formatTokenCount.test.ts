import { describe, it, expect } from 'vitest'
import { formatTokenCount } from '../lib/formatTokenCount'

describe('formatTokenCount', () => {
  describe('numbers below 1 000 — returned as locale strings', () => {
    it('formats 0', () => {
      expect(formatTokenCount(0)).toBe('0')
    })

    it('formats a small number', () => {
      expect(formatTokenCount(42)).toBe('42')
    })

    it('formats 999 (upper boundary)', () => {
      expect(formatTokenCount(999)).toBe('999')
    })
  })

  describe('numbers from 1 000 to 999 999 — formatted as X.YK', () => {
    it('formats 1 000 (lower boundary)', () => {
      expect(formatTokenCount(1_000)).toBe('1.0K')
    })

    it('formats a mid-range value', () => {
      expect(formatTokenCount(892_340)).toBe('892.3K')
    })

    it('formats 999 999 (upper boundary)', () => {
      expect(formatTokenCount(999_999)).toBe('1000.0K')
    })

    it('truncates rather than rounds up the decimal', () => {
      // 1 550 / 1 000 = 1.55 -> toFixed(1) = "1.6"
      expect(formatTokenCount(1_550)).toBe('1.6K')
    })
  })

  describe('numbers >= 1 000 000 — formatted as X.YM', () => {
    it('formats 1 000 000 (lower boundary)', () => {
      expect(formatTokenCount(1_000_000)).toBe('1.0M')
    })

    it('formats a mid-range value', () => {
      expect(formatTokenCount(3_241_560)).toBe('3.2M')
    })

    it('formats a very large number', () => {
      expect(formatTokenCount(150_000_000)).toBe('150.0M')
    })
  })

  describe('negative numbers', () => {
    it('formats a negative number below 1 000 as a locale string', () => {
      // n < 1_000 is true for negatives, so toLocaleString is used
      expect(formatTokenCount(-5)).toBe('-5')
    })
  })
})

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useChartTheme } from '../useChartTheme'
import { useTheme } from '../useTheme'

const chartTokens = Array.from({ length: 8 }, (_, index) => `--color-chart-${index + 1}`)
const roomIds = [
  '00000000-0000-0000-0000-000000000001',
  '00000000-0000-0000-0000-000000000002',
  '00000000-0000-0000-0000-000000000003',
  '00000000-0000-0000-0000-000000000004',
  '00000000-0000-0000-0000-000000000005',
  '00000000-0000-0000-0000-000000000006',
  '00000000-0000-0000-0000-000000000007',
  '00000000-0000-0000-0000-000000000008',
]

describe('useChartTheme', () => {
  beforeEach(() => {
    chartTokens.forEach((name, index) => {
      document.documentElement.style.setProperty(name, `#00000${index + 1}`)
    })
    document.documentElement.style.setProperty('--color-muted', '#123456')
    document.documentElement.style.setProperty('--color-border-subtle', '#234567')
    document.documentElement.style.setProperty('--color-fg', '#345678')
    useTheme().setTheme('light')
  })

  afterEach(() => {
    chartTokens.forEach((name) => document.documentElement.style.removeProperty(name))
    document.documentElement.style.removeProperty('--color-muted')
    document.documentElement.style.removeProperty('--color-border-subtle')
    document.documentElement.style.removeProperty('--color-fg')
  })

  it('reads reactive chart chrome tokens and returns stable room colours', () => {
    const chartTheme = useChartTheme()
    expect(chartTheme.tickColor.value).toBe('#123456')
    expect(chartTheme.gridColor.value).toBe('#234567')
    expect(chartTheme.legendColor.value).toBe('#345678')
    expect(chartTheme.seriesColor(roomIds[0])).toBe(chartTheme.seriesColor(roomIds[0]))
    expect(new Set(roomIds.map((id) => chartTheme.seriesColor(id))).size).toBeGreaterThanOrEqual(7)
  })
})

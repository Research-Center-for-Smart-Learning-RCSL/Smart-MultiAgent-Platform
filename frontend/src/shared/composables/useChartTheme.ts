import { computed } from 'vue'
import { useTheme } from './useTheme'

function readToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function hashKey(key: string): number {
  let hash = 0x811c9dc5
  for (let index = 0; index < key.length; index++) {
    hash = Math.imul(hash ^ key.charCodeAt(index), 0x01000193)
  }
  return hash >>> 0
}

export function useChartTheme() {
  const { theme } = useTheme()
  const tickColor = computed(() => {
    void theme.value
    return readToken('--color-muted')
  })
  const gridColor = computed(() => {
    void theme.value
    return readToken('--color-border-subtle')
  })
  const legendColor = computed(() => {
    void theme.value
    return readToken('--color-fg')
  })

  function seriesColor(key: string): string {
    void theme.value
    const tokenIndex = (hashKey(key) % 8) + 1
    return readToken(`--color-chart-${tokenIndex}`)
  }

  return { tickColor, gridColor, legendColor, seriesColor }
}

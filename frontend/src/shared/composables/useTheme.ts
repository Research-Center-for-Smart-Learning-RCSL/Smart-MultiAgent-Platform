import { ref, watchEffect } from 'vue'

export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'smap-theme'

function supportsMatchMedia(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
}

function getSystemPreference(): 'light' | 'dark' {
  if (!supportsMatchMedia()) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function readStored(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch { /* SSR / restricted storage */ }
  return 'system'
}

const theme = ref<Theme>(readStored())

function applyTheme(): void {
  const resolved = theme.value === 'system' ? getSystemPreference() : theme.value
  document.documentElement.setAttribute('data-theme', resolved)
}

if (supportsMatchMedia()) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (theme.value === 'system') applyTheme()
  })
}

export function useTheme() {
  watchEffect(() => {
    localStorage.setItem(STORAGE_KEY, theme.value)
    applyTheme()
  })

  return {
    theme,
    setTheme: (t: Theme) => { theme.value = t },
  }
}

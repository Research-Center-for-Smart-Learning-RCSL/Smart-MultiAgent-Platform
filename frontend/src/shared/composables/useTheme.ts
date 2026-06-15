import { ref, watch } from 'vue'

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

function persist(t: Theme): void {
  try { localStorage.setItem(STORAGE_KEY, t) } catch { /* quota / restricted */ }
}

function applyTheme(): void {
  if (typeof document === 'undefined') return
  const resolved = theme.value === 'system' ? getSystemPreference() : theme.value
  document.documentElement.setAttribute('data-theme', resolved)
}

const theme = ref<Theme>(readStored())

applyTheme()

watch(theme, (v) => {
  persist(v)
  applyTheme()
})

if (supportsMatchMedia()) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (theme.value === 'system') applyTheme()
  })
}

export function useTheme() {
  return {
    theme,
    setTheme: (t: Theme) => { theme.value = t },
  }
}

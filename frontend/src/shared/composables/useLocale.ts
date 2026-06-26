import { watch } from 'vue'
import { i18n, ensureLocaleLoaded, type Locale } from '@shared/i18n'

const STORAGE_KEY = 'smap-locale'
const SUPPORTED: Locale[] = ['en', 'zh-TW']

function detectBrowserLocale(): Locale {
  return navigator.language.startsWith('zh') ? 'zh-TW' : 'en'
}

function readStored(): Locale | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'en' || v === 'zh-TW') return v
  } catch { /* restricted storage */ }
  return null
}

function persist(locale: Locale): void {
  try { localStorage.setItem(STORAGE_KEY, locale) } catch { /* quota / restricted */ }
}

const stored = readStored()
if (stored && stored !== i18n.global.locale.value) {
  i18n.global.locale.value = stored
  ensureLocaleLoaded(stored)
}

watch(() => i18n.global.locale.value, (v) => {
  persist(v as Locale)
})

function setLocale(locale: Locale): void {
  ensureLocaleLoaded(locale).then(() => {
    i18n.global.locale.value = locale
  })
}

function cycleLocale(): void {
  const current = i18n.global.locale.value as Locale
  const idx = SUPPORTED.indexOf(current)
  setLocale(SUPPORTED[(idx + 1) % SUPPORTED.length]!)
}

export function useLocale() {
  return {
    locale: i18n.global.locale,
    setLocale,
    cycleLocale,
  }
}

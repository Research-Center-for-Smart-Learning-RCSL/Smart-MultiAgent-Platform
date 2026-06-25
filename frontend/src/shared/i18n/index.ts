import { createI18n } from 'vue-i18n'
import { watch } from 'vue'

export type Locale = 'en' | 'zh-TW'
type Messages = Record<string, unknown>
type MessageLoader = () => Promise<{ default: Messages }>

const FALLBACK_LOCALE: Locale = 'en'

// Registered per-(slice) loaders, grouped by language. Each loader's fetch+merge
// is memoized so concurrent or repeated ensureLocaleLoaded() calls share one
// in-flight promise (and the browser dedups to one request per chunk).
const loaders: Record<Locale, MessageLoader[]> = { en: [], 'zh-TW': [] }
const loaderRuns = new Map<MessageLoader, Promise<void>>()
let fallbackRequested = false

export const i18n = createI18n<false, Messages>({
  legacy: false,
  locale: (navigator.language.startsWith('zh') ? 'zh-TW' : 'en') as Locale,
  fallbackLocale: FALLBACK_LOCALE,
  messages: { en: {}, 'zh-TW': {} },
  // Only the active locale is loaded at boot. If a key is unresolved (missing in
  // the active locale and the English fallback isn't loaded yet — e.g. a stale
  // translation or a failed active-locale chunk), pull the fallback bundles in
  // once; vue-i18n re-resolves against the merged fallback on the next render.
  missing: (_locale, key) => {
    if (!fallbackRequested) {
      fallbackRequested = true
      ensureLocaleLoaded(FALLBACK_LOCALE).catch(() => {
        fallbackRequested = false // allow a retry on the next missing key
      })
    }
    return key
  },
})

export function registerLocaleLoaders(map: Record<Locale, MessageLoader>): void {
  loaders.en.push(map.en)
  loaders['zh-TW'].push(map['zh-TW'])
}

function runLoader(locale: Locale, loader: MessageLoader): Promise<void> {
  let run = loaderRuns.get(loader)
  if (!run) {
    run = loader()
      .then((mod) => {
        i18n.global.mergeLocaleMessage(locale, mod.default)
      })
      .catch((err) => {
        loaderRuns.delete(loader) // drop the cached failure so a retry can run
        throw err
      })
    loaderRuns.set(loader, run)
  }
  return run
}

/**
 * Fetch + merge every registered bundle for `locale`. Each loader runs at most
 * once (memoized), so this is safe to call repeatedly and concurrently; loaders
 * registered after a previous call run on the next call.
 */
export function ensureLocaleLoaded(locale: Locale): Promise<void> {
  return Promise.all(loaders[locale].map((l) => runLoader(locale, l))).then(() => undefined)
}

export function syncHtmlLang(): void {
  const update = (lang: string) => {
    document.documentElement.setAttribute('lang', lang)
  }
  update(i18n.global.locale.value)
  watch(() => i18n.global.locale.value, update)
}

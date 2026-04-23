// vue-i18n setup. Slices merge their locales via `registerMessages`.

import { createI18n } from 'vue-i18n'

type Locale = 'en' | 'zh-TW'
type Messages = Record<string, unknown>

const merged: Record<Locale, Messages> = { en: {}, 'zh-TW': {} }

export const i18n = createI18n<false, Messages>({
  legacy: false,
  locale: (navigator.language.startsWith('zh') ? 'zh-TW' : 'en') as Locale,
  fallbackLocale: 'en',
  messages: merged,
})

export function registerMessages(locale: Locale, messages: Messages): void {
  i18n.global.mergeLocaleMessage(locale, messages)
}

import { createI18n } from 'vue-i18n'
import { watch } from 'vue'

type Locale = 'en' | 'zh-TW'
type Messages = Record<string, unknown>

const merged: Record<Locale, Messages> = { en: {}, 'zh-TW': {} }

export const SUPPORTED_LOCALES: Locale[] = ['en', 'zh-TW']

export const i18n = createI18n<false, Messages>({
  legacy: false,
  locale: (navigator.language.startsWith('zh') ? 'zh-TW' : 'en') as Locale,
  fallbackLocale: 'en',
  messages: merged,
})

export function registerMessages(locale: Locale, messages: Messages): void {
  i18n.global.mergeLocaleMessage(locale, messages)
}

export function syncHtmlLang(): void {
  const update = (lang: string) => {
    document.documentElement.setAttribute('lang', lang)
  }
  update(i18n.global.locale.value)
  watch(() => i18n.global.locale.value, update)
}

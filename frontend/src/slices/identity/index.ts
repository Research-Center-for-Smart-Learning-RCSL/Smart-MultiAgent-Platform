import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { identityRoutes } from './routes'
export { useSessionStore } from './stores/session'
export { identityKeys } from './queries'

export function installIdentitySlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

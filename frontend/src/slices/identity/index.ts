import { registerLocaleLoaders } from '@shared/i18n'

export { identityRoutes } from './routes'
export { useSessionStore } from './stores/session'
export { identityKeys } from './queries'

export function installIdentitySlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

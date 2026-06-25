import { registerLocaleLoaders } from '@shared/i18n'

export { adminRoutes } from './routes'
export { useAdminStore } from './stores/admin'
export { adminKeys } from './queries'
export { default as ImpersonationBanner } from './components/ImpersonationBanner.vue'

export function installAdminSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

import { registerLocaleLoaders } from '@shared/i18n'

export { adminRoutes } from './routes'
export { useAdminStore } from './stores/admin'
export { adminKeys } from './queries'
export { default as ImpersonationBanner } from './components/ImpersonationBanner.vue'
// App.vue needs the flag, not the banner: while an admin is impersonating the
// banner is the topmost element and owns the safe-area strip, which the shell
// expresses as a class on .app-root (main.css, --topbar-inset-top). The flag
// form and not useImpersonation() itself — App.vue is mounted above the
// QueryClient provider, so the mutations that composable builds cannot exist
// there.
export { useImpersonationFlag } from './composables/useImpersonation'

export function installAdminSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

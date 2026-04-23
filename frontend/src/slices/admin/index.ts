import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { adminRoutes } from './routes'
export { useAdminStore } from './stores/admin'
export { default as ImpersonationBanner } from './components/ImpersonationBanner.vue'

export function installAdminSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

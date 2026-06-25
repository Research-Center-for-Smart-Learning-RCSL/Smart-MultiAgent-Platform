// Public surface of the notifications slice (R18). Only exports listed here are
// importable from other slices / the app shell.
import { registerLocaleLoaders } from '@shared/i18n'

export { notificationsRoutes } from './routes'
export { notificationKeys } from './queries'
export { default as NotificationBell } from './components/NotificationBell.vue'
export type { Notification } from './api'

export function installNotificationsSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

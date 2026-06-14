// Public surface of the notifications slice (R18). Only exports listed here are
// importable from other slices / the app shell.
import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { notificationsRoutes } from './routes'
export { notificationKeys } from './queries'
export { default as NotificationBell } from './components/NotificationBell.vue'
export type { Notification } from './api'

export function installNotificationsSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

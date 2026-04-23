import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { tenancyRoutes } from './routes'

export function installTenancySlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

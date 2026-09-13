import { registerLocaleLoaders } from '@shared/i18n'

export { dashboardRoutes } from './routes'

export function installDashboardSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

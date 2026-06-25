import { registerLocaleLoaders } from '@shared/i18n'

export { tenancyRoutes } from './routes'
export { tenancyKeys } from './queries'
export { orgsApi } from './api/orgs'
export type { Org } from './api/orgs'
export { projectsApi } from './api/projects'
export type { Project } from './api/projects'

export function installTenancySlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

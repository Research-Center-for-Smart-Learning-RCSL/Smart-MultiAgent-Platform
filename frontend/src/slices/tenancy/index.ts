import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { tenancyRoutes } from './routes'
export { tenancyKeys } from './queries'
export { orgsApi } from './api/orgs'
export type { Org } from './api/orgs'
export { projectsApi } from './api/projects'
export type { Project } from './api/projects'

export function installTenancySlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

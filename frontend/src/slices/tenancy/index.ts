import { registerLocaleLoaders } from '@shared/i18n'

export { tenancyRoutes } from './routes'
export { tenancyKeys } from './queries'
export { orgsApi } from './api/orgs'
export type { Org } from './api/orgs'
export { projectsApi } from './api/projects'
export type { Project } from './api/projects'
// Re-exported for the conversation slice's room-settings view, which binds a
// room to this project's member groups (section 13.2a).
export { memberGroupsApi } from './api/memberGroups'
export type { MemberGroup } from './api/memberGroups'
export { useProjectRole } from './composables/useProjectRole'

export function installTenancySlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

import { registerLocaleLoaders } from '@shared/i18n'

export { agentGroupsRoutes } from './routes'
export { agentGroupKeys } from './queries'
export { agentGroupsApi } from './api'
export type { AgentGroup } from './api'

export function installAgentGroupsSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

import { registerLocaleLoaders } from '@shared/i18n'

export { agentsRoutes } from './routes'
export { agentKeys } from './queries'
export { agentsApi } from './api'
export type { Agent, RagConfig } from './api'
export { useRagConfigSocket, type RagIngestionProgress } from './composables/useRagConfigSocket'
export { agentCreateSchema, ragConfigCreateSchema } from './types/schemas'
export type { AgentCreateInput, RagConfigCreateInput } from './types/schemas'

export function installAgentsSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}

import { registerMessages } from '@shared/i18n'
import en from './locales/en.json'
import zhTW from './locales/zh-TW.json'

export { agentsRoutes } from './routes'
export { agentKeys } from './queries'
export { agentsApi } from './api'
export type { Agent, RagConfig } from './api'
export { useRagConfigSocket, type RagIngestionProgress } from './composables/useRagConfigSocket'
export { agentCreateSchema, ragConfigCreateSchema } from './types/schemas'
export type { AgentCreateInput, RagConfigCreateInput } from './types/schemas'

export function installAgentsSlice(): void {
  registerMessages('en', en)
  registerMessages('zh-TW', zhTW)
}

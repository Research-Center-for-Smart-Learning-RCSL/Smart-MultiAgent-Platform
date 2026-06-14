// Public surface of the keys slice. Only exports listed here are
// importable from other slices (enforced in Phase J).
import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { keysRoutes } from './routes'
export { keysKeys } from './queries'
export { keyGroupsApi } from './api'
// Project-carried keys + the capability table are part of the public surface so
// the agents slice can source embedding / rerank keys for RAG-config forms.
export { projectKeysApi, CAPABILITIES } from './api'
export type { ApiKey, ApiKeyProvider, ProviderCapability } from './api'
export type { SearchKey, SearchProvider } from './api'
export type { KeyGroup, KeyGroupDetail, KeyGroupMember } from './api'

export function installKeysSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

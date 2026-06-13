// Public surface of the keys slice. Only exports listed here are
// importable from other slices (enforced in Phase J).
import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { keysRoutes } from './routes'
export { keysKeys } from './queries'
export { keyGroupsApi } from './api'
export type { ApiKey, ApiKeyProvider, ProviderCapability } from './api'
export type { SearchKey, SearchProvider } from './api'
export type { KeyGroup, KeyGroupDetail, KeyGroupMember } from './api'

export function installKeysSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

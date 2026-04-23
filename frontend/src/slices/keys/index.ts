// Public surface of the keys slice. Only exports listed here are
// importable from other slices (enforced in Phase J).
import { registerMessages } from '@shared/i18n'
import enMessages from './locales/en.json'
import zhMessages from './locales/zh-TW.json'

export { keysRoutes } from './routes'
export type { ApiKey, ApiKeyProvider, ProviderCapability } from './api/keys'
export type { SearchKey, SearchProvider } from './api/search-keys'
export type { KeyGroup, KeyGroupMember } from './api/key-groups'

export function installKeysSlice(): void {
  registerMessages('en', enMessages)
  registerMessages('zh-TW', zhMessages)
}

import { KeysService } from '@shared/api-client'
import type { ApiKey } from './keys'

// Mirrors backend `UsageOut` (R7.05 aggregate; no secret exposed).
export type UsageWindow = '1h' | '24h' | '7d' | '30d'
export interface KeyUsage {
  window: string
  input_tokens: number
  output_tokens: number
  requests: number
  errors: number
}

// Project-scoped carry surface, backed by the same generated KeysService (R24.13).
export const projectKeysApi = {
  listCarried: (projectId: string): Promise<ApiKey[]> =>
    KeysService.listCarriedKeysApiProjectsProjectIdKeysGet({ projectId }),
  carry: (projectId: string, keyId: string): Promise<void> =>
    KeysService.carryKeyApiProjectsProjectIdKeysPost({ projectId, requestBody: { key_id: keyId } }),
  withdraw: (projectId: string, keyId: string): Promise<void> =>
    KeysService.withdrawKeyApiProjectsProjectIdKeysKeyIdDelete({ projectId, keyId }),
  usage: (projectId: string, keyId: string, window: UsageWindow): Promise<KeyUsage> =>
    KeysService.readUsageApiProjectsProjectIdKeysKeyIdUsageGet({ projectId, keyId, window }),
}

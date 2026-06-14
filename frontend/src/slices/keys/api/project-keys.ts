import { http } from '@shared/transport'
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

export const projectKeysApi = {
  listCarried: (projectId: string) =>
    http.get<ApiKey[]>(`/projects/${projectId}/keys`),
  carry: (projectId: string, keyId: string) =>
    http.post(`/projects/${projectId}/keys`, { key_id: keyId }),
  withdraw: (projectId: string, keyId: string) =>
    http.delete(`/projects/${projectId}/keys/${keyId}`),
  usage: (projectId: string, keyId: string, window: UsageWindow) =>
    http.get<KeyUsage>(`/projects/${projectId}/keys/${keyId}/usage`, {
      params: { window },
    }),
}

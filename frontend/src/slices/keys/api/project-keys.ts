import { http } from '@shared/transport'
import type { ApiKey } from './keys'

export const projectKeysApi = {
  listCarried: (projectId: string) =>
    http.get<ApiKey[]>(`/projects/${projectId}/keys`),
  carry: (projectId: string, keyId: string) =>
    http.post(`/projects/${projectId}/keys`, { key_id: keyId }),
  withdraw: (projectId: string, keyId: string) =>
    http.delete(`/projects/${projectId}/keys/${keyId}`),
}

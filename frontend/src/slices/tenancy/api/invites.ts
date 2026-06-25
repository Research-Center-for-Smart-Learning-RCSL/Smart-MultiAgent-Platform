import { http } from '@shared/transport'
import type { PaginationParams } from '@shared/transport'

export interface Invite {
  id: string
  scope_type: 'org' | 'project'
  scope_id: string
  scope_name: string
  invitee_email: string
  role: 'owner' | 'member'
  state: 'pending' | 'accepted' | 'rejected' | 'revoked' | 'expired'
  created_at: string
  expires_at: string
}

export const invitesApi = {
  list: (state: 'pending' | 'accepted' | 'rejected' = 'pending', params?: PaginationParams) =>
    http.get<Invite[]>(`/invites`, { params: { state, ...params } }),
  accept: (id: string) => http.post(`/invites/${id}/accept`),
  acceptByToken: (token: string) => http.post<Invite>(`/invites/accept-by-token`, { token }),
  reject: (id: string) => http.post(`/invites/${id}/reject`),
}

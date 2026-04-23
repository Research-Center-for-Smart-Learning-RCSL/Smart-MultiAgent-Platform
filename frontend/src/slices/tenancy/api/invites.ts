import { http } from '@shared/transport'

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
  list: (state: 'pending' | 'accepted' | 'rejected' = 'pending') =>
    http.get<Invite[]>(`/invites`, { params: { state } }),
  accept: (id: string) => http.post(`/invites/${id}/accept`),
  reject: (id: string) => http.post(`/invites/${id}/reject`),
}

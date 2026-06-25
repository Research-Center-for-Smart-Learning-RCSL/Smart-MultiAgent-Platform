import { http } from '@shared/transport'

export interface PaginationParams {
  limit?: number
  offset?: number
}

export interface Org {
  id: string
  name: string
  creator_user_id: string
  default_project_id?: string | null
  version: number
  created_at: string
  deleted_at?: string | null
}

export interface OrgMember {
  user_id: string
  email: string
  role: 'owner' | 'member'
  is_original_creator: boolean
  joined_at: string
}

export interface OrgQuotas {
  users: number
  projects: number
  chatrooms: number
  agents: number
  workflows: number
  computed_at: string | null
  advisory_targets: Record<string, number>
}

export interface OriginalCreatorTransfer {
  id: string
  org_id: string
  initiator_user_id: string
  target_user_id: string
  state: 'pending' | 'accepted' | 'rejected' | 'cancelled' | 'expired' | 'admin_forced'
  created_at: string
  expires_at: string
}

export const orgsApi = {
  list: (params?: PaginationParams) =>
    http.get<Org[]>('/orgs', { params }),
  create: (name: string) => http.post<Org>('/orgs', { name }),
  get: (id: string) => http.get<Org>(`/orgs/${id}`),
  rename: (id: string, name: string, version: number) =>
    http.patch<Org>(`/orgs/${id}`, { name }, { headers: { 'If-Match': String(version) } }),
  remove: (id: string) => http.delete(`/orgs/${id}`),
  restore: (id: string) => http.post<Org>(`/orgs/${id}/restore`),
  quotas: (id: string) => http.get<OrgQuotas>(`/orgs/${id}/quotas`),

  listMembers: (id: string, params?: PaginationParams) =>
    http.get<OrgMember[]>(`/orgs/${id}/members`, { params }),
  removeMember: (id: string, uid: string) => http.delete(`/orgs/${id}/members/${uid}`),
  setRole: (id: string, uid: string, role: 'owner' | 'member') =>
    http.patch(`/orgs/${id}/members/${uid}`, { role }),

  invite: (id: string, email: string, role: 'owner' | 'member') =>
    http.post(`/orgs/${id}/invites`, { email, role }),

  initiateTransfer: (id: string, target_user_id: string) =>
    http.post<OriginalCreatorTransfer>(
      `/orgs/${id}/original-creator-transfers`,
      { target_user_id },
    ),
  listTransfers: (id: string, params?: PaginationParams) =>
    http.get<OriginalCreatorTransfer[]>(`/orgs/${id}/original-creator-transfers`, { params }),
  acceptTransfer: (id: string, tid: string) =>
    http.post(`/orgs/${id}/original-creator-transfers/${tid}/accept`),
  cancelTransfer: (id: string, tid: string) =>
    http.delete(`/orgs/${id}/original-creator-transfers/${tid}`),
  rejectTransfer: (id: string, tid: string) =>
    http.post(`/orgs/${id}/original-creator-transfers/${tid}/reject`),
}

import { http } from '@shared/transport'
import type {
  AdminEntry,
  AuditFilter,
  AuditPage,
  ImpersonateResult,
  IpBan,
  Metrics,
  OrgSummary,
  ProjectSummary,
  RateLimitPolicy,
  UserDetail,
  UserSummary,
} from '../types'

export const adminApi = {
  listUsers: (params?: { q?: string; status?: string; cursor?: string; limit?: number }) =>
    http.get<UserSummary[]>('/admin/users', { params }).then(r => r.data),

  getUser: (id: string) =>
    http.get<UserDetail>(`/admin/users/${id}`).then(r => r.data),

  banUser: (id: string, reason: string) =>
    http.post(`/admin/users/${id}/ban`, { reason }).then(r => r.data),

  unbanUser: (id: string) =>
    http.post(`/admin/users/${id}/unban`).then(r => r.data),

  softDeleteUser: (id: string) =>
    http.post(`/admin/users/${id}/delete`).then(r => r.data),

  hardDeleteUser: (id: string) =>
    http.post(`/admin/users/${id}/hard-delete`).then(r => r.data),

  impersonate: (id: string) =>
    http.post<ImpersonateResult>(`/admin/users/${id}/impersonate`).then(r => r.data),

  endImpersonate: (id: string) =>
    http.post(`/admin/users/${id}/end-impersonate`).then(r => r.data),

  listAdmins: () =>
    http.get<AdminEntry[]>('/admin/admins').then(r => r.data),

  promoteAdmin: (userId: string) =>
    http.post<AdminEntry>('/admin/admins', { user_id: userId }).then(r => r.data),

  demoteAdmin: (userId: string) =>
    http.delete(`/admin/admins/${userId}`).then(r => r.data),

  listOrgs: (params?: { cursor?: string; limit?: number }) =>
    http.get<OrgSummary[]>('/admin/orgs', { params }).then(r => r.data),

  forceDeleteOrg: (orgId: string) =>
    http.post(`/admin/orgs/${orgId}/force-delete`).then(r => r.data),

  forceTransferOC: (orgId: string, targetUserId: string) =>
    http.post(`/admin/orgs/${orgId}/force-transfer-original-creator`, {
      target_user_id: targetUserId,
    }).then(r => r.data),

  listProjects: (params?: { cursor?: string; limit?: number }) =>
    http.get<ProjectSummary[]>('/admin/projects', { params }).then(r => r.data),

  queryAudit: (filters: AuditFilter) =>
    http.get<AuditPage>('/admin/audit', { params: filters }).then(r => r.data),

  exportAudit: (filters: Partial<AuditFilter>) =>
    http.post<{ url: string; job_id: string }>('/admin/audit/export', null, { params: filters }).then(r => r.data),

  restoreResource: (type: string, id: string) =>
    http.post<{ restored: boolean }>(`/admin/restore/${type}/${id}`).then(r => r.data),

  getMetrics: () =>
    http.get<Metrics>('/admin/metrics').then(r => r.data),

  listRateLimits: () =>
    http.get<RateLimitPolicy[]>('/admin/rate-limits').then(r => r.data),

  patchRateLimit: (key: string, patch: { window_sec?: number; max_count?: number; scope?: string }) =>
    http.patch<RateLimitPolicy>(`/admin/rate-limits/${key}`, patch).then(r => r.data),

  resetGraphrag: (configId: string) =>
    http.post(`/admin/graphrag/${configId}/reset`).then(r => r.data),

  listIpBans: () =>
    http.get<IpBan[]>('/admin/ip-bans').then(r => r.data),

  createIpBan: (cidr: string, reason: string) =>
    http.post<IpBan>('/admin/ip-bans', { cidr, reason }).then(r => r.data),

  deleteIpBan: (id: string) =>
    http.delete(`/admin/ip-bans/${id}`).then(r => r.data),
}

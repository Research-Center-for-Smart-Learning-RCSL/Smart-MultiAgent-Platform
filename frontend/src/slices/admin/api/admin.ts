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
    http.get<UserSummary[]>('/admin/users', { params }),

  getUser: (id: string) =>
    http.get<UserDetail>(`/admin/users/${id}`),

  banUser: (id: string, reason: string) =>
    http.post(`/admin/users/${id}/ban`, { reason }),

  unbanUser: (id: string) =>
    http.post(`/admin/users/${id}/unban`),

  softDeleteUser: (id: string) =>
    http.post(`/admin/users/${id}/delete`),

  hardDeleteUser: (id: string) =>
    http.post(`/admin/users/${id}/hard-delete`),

  impersonate: (id: string) =>
    http.post<ImpersonateResult>(`/admin/users/${id}/impersonate`),

  endImpersonate: (id: string) =>
    http.post(`/admin/users/${id}/end-impersonate`),

  listAdmins: () =>
    http.get<AdminEntry[]>('/admin/admins'),

  promoteAdmin: (userId: string) =>
    http.post<AdminEntry>('/admin/admins', { user_id: userId }),

  demoteAdmin: (userId: string) =>
    http.delete(`/admin/admins/${userId}`),

  listOrgs: (params?: { cursor?: string; limit?: number }) =>
    http.get<OrgSummary[]>('/admin/orgs', { params }),

  forceDeleteOrg: (orgId: string) =>
    http.post(`/admin/orgs/${orgId}/force-delete`),

  forceTransferOC: (orgId: string, targetUserId: string) =>
    http.post(`/admin/orgs/${orgId}/force-transfer-original-creator`, {
      target_user_id: targetUserId,
    }),

  listProjects: (params?: { cursor?: string; limit?: number }) =>
    http.get<ProjectSummary[]>('/admin/projects', { params }),

  queryAudit: (filters: AuditFilter) =>
    http.get<AuditPage>('/admin/audit', { params: filters }),

  exportAudit: (filters: Partial<AuditFilter>) =>
    http.post<{ url: string; job_id: string }>('/admin/audit/export', null, { params: filters }),

  restoreResource: (type: string, id: string) =>
    http.post<{ restored: boolean }>(`/admin/restore/${type}/${id}`),

  getMetrics: () =>
    http.get<Metrics>('/admin/metrics'),

  listRateLimits: () =>
    http.get<RateLimitPolicy[]>('/admin/rate-limits'),

  patchRateLimit: (key: string, patch: { window_sec?: number; max_count?: number; scope?: string }) =>
    http.patch<RateLimitPolicy>(`/admin/rate-limits/${key}`, patch),

  resetGraphrag: (configId: string) =>
    http.post(`/admin/graphrag/${configId}/reset`),

  listIpBans: () =>
    http.get<IpBan[]>('/admin/ip-bans'),

  createIpBan: (cidr: string, reason: string) =>
    http.post<IpBan>('/admin/ip-bans', { cidr, reason }),

  deleteIpBan: (id: string) =>
    http.delete(`/admin/ip-bans/${id}`),
}

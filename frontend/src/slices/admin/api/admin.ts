// Admin API (admin slice, admin-only privileged surface).
//
// Wraps the generated AdminService (+ GraphragAdminService for the one graphrag-reset
// route) over the one instrumented axios singleton. The methods already returned bare
// bodies (they unwrapped `.data`), so this conversion is signature-preserving — consumers
// are untouched. Every generated *Out is assignable to the hand-rolled slice type, so the
// wrappers annotate the hand-rolled type and return the call directly; the few casts below
// relocate assertions the previous `http.<verb><T>()` calls already made.

import { AdminService, GraphragAdminService } from '@shared/api-client'
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

// AuditFilter is snake_case (it mirrors the backend query names); the generated audit
// endpoints take camelCase options they re-map to snake_case. Coalesce absent fields to
// null — the generated query builder drops null (and undefined) values, so only the set
// filters ship, exactly as the previous `{ params: filters }` did.
//
// Keep this map in sync with AuditFilter: a filterable field added there but not mapped here
// is silently dropped from the query (the old direct `{ params: filters }` spread included
// new fields automatically; this explicit camelCase rename does not).
function auditFilterToOptions(f: Partial<AuditFilter>) {
  return {
    actorUserId: f.actor_user_id ?? null,
    resourceType: f.resource_type ?? null,
    resourceId: f.resource_id ?? null,
    action: f.action ?? null,
    from: f.from ?? null,
    to: f.to ?? null,
    ipPrefix: f.ip_prefix ?? null,
    sessionId: f.session_id ?? null,
    requestId: f.request_id ?? null,
  }
}

export const adminApi = {
  listUsers: (params?: { q?: string; status?: string; cursor?: string; limit?: number }): Promise<UserSummary[]> =>
    AdminService.listUsersApiAdminUsersGet(params ?? {}),

  getUser: (id: string): Promise<UserDetail> =>
    AdminService.getUserApiAdminUsersUserIdGet({ userId: id }),

  banUser: (id: string, reason: string) =>
    AdminService.banUserApiAdminUsersUserIdBanPost({ userId: id, requestBody: { reason } }),

  unbanUser: (id: string) => AdminService.unbanUserApiAdminUsersUserIdUnbanPost({ userId: id }),

  softDeleteUser: (id: string) =>
    AdminService.softDeleteUserApiAdminUsersUserIdDeletePost({ userId: id }),

  hardDeleteUser: (id: string) =>
    AdminService.hardDeleteUserApiAdminUsersUserIdHardDeletePost({ userId: id }),

  impersonate: (id: string): Promise<ImpersonateResult> =>
    AdminService.impersonateApiAdminUsersUserIdImpersonatePost({ userId: id }),

  endImpersonate: (id: string) =>
    AdminService.endImpersonateApiAdminUsersUserIdEndImpersonatePost({ userId: id }),

  listAdmins: (): Promise<AdminEntry[]> => AdminService.listAdminsApiAdminAdminsGet(),

  promoteAdmin: (userId: string): Promise<AdminEntry> =>
    AdminService.promoteAdminApiAdminAdminsPost({ requestBody: { user_id: userId } }),

  demoteAdmin: (userId: string) =>
    AdminService.demoteAdminApiAdminAdminsUserIdDelete({ userId }),

  listOrgs: (params?: { cursor?: string; limit?: number }): Promise<OrgSummary[]> =>
    AdminService.listOrgsApiAdminOrgsGet(params ?? {}),

  forceDeleteOrg: (orgId: string) =>
    AdminService.forceDeleteOrgApiAdminOrgsOrgIdForceDeletePost({ orgId }),

  forceTransferOC: (orgId: string, targetUserId: string) =>
    AdminService.forceTransferOcApiAdminOrgsOrgIdForceTransferOriginalCreatorPost({
      orgId,
      requestBody: { target_user_id: targetUserId },
    }),

  listProjects: (params?: { cursor?: string; limit?: number }): Promise<ProjectSummary[]> =>
    AdminService.listProjectsApiAdminProjectsGet(params ?? {}),

  queryAudit: (filters: AuditFilter): Promise<AuditPage> =>
    AdminService.queryAuditApiAdminAuditGet({
      ...auditFilterToOptions(filters),
      cursor: filters.cursor ?? null,
      ...(filters.limit !== undefined ? { limit: filters.limit } : {}),
    }),

  exportAudit: (filters: Partial<AuditFilter>): Promise<{ url: string; job_id: string }> =>
    AdminService.exportAuditApiAdminAuditExportPost(auditFilterToOptions(filters)).then(
      (r) => r as { url: string; job_id: string },
    ),

  // The generated resourceType is typed to three values, but the admin UI restores six
  // (user/org/project/agent/workflow/chatroom); cast so the raw string reaches the path as
  // before. The narrow OpenAPI enum is a backend defect tracked as FU-4.
  restoreResource: (type: string, id: string): Promise<{ restored: boolean }> =>
    AdminService.restoreResourceApiAdminRestoreResourceTypeResourceIdPost({
      resourceType: type as 'user' | 'org' | 'project',
      resourceId: id,
    }),

  getMetrics: (): Promise<Metrics> => AdminService.adminMetricsApiAdminMetricsGet(),

  listRateLimits: (): Promise<RateLimitPolicy[]> =>
    AdminService.listRateLimitsApiAdminRateLimitsGet({}),

  patchRateLimit: (
    key: string,
    patch: { window_sec?: number; max_count?: number; scope?: string },
  ): Promise<RateLimitPolicy> =>
    AdminService.patchRateLimitApiAdminRateLimitsKeyPatch({ key, requestBody: patch }),

  resetGraphrag: (configId: string) =>
    GraphragAdminService.adminResetApiAdminGraphragConfigIdResetPost({ configId }),

  listIpBans: (): Promise<IpBan[]> => AdminService.listBansApiAdminIpBansGet(),

  createIpBan: (cidr: string, reason: string): Promise<IpBan> =>
    AdminService.addBanApiAdminIpBansPost({ requestBody: { cidr, reason } }),

  deleteIpBan: (id: string) => AdminService.removeBanApiAdminIpBansBanIdDelete({ banId: id }),
}

import type { AuditFilter } from '../types'

export const adminKeys = {
  users: (params?: { q?: string; status?: string }) =>
    ['admin', 'users', params] as const,
  user: (id: string) =>
    ['admin', 'user', id] as const,
  admins: () =>
    ['admin', 'admins'] as const,
  orgs: () =>
    ['admin', 'orgs'] as const,
  projects: () =>
    ['admin', 'projects'] as const,
  audit: (filters: AuditFilter) =>
    ['admin', 'audit', filters] as const,
  metrics: () =>
    ['admin', 'metrics'] as const,
  rateLimits: () =>
    ['admin', 'rate-limits'] as const,
  ipBans: () =>
    ['admin', 'ip-bans'] as const,
}

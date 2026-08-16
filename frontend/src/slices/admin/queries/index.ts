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
  activityTypes: () =>
    ['admin', 'activity-types'] as const,
  // Deliberately its own entry rather than a variant of activityTypes(): the
  // governance table shares that one, and a platform-only list cached under it
  // would make the table claim the platform rows are all that exist.
  platformActivityTypes: () =>
    ['admin', 'platform-activity-types'] as const,
  activityActivations: () =>
    ['admin', 'activity-activations'] as const,
  activityPolicy: () =>
    ['admin', 'activity-policy'] as const,
  activityExamples: () =>
    ['admin', 'activity-examples'] as const,
}

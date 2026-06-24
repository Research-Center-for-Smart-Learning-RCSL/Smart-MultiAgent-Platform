export const tenancyKeys = {
  orgs: () => ['tenancy', 'orgs'] as const,
  org: (id: string) => ['tenancy', 'org', id] as const,
  orgQuotas: (id: string) => ['tenancy', 'orgQuotas', id] as const,
  orgMembers: (orgId: string) => ['tenancy', 'orgMembers', orgId] as const,
  orgTransfers: (orgId: string) => ['tenancy', 'orgTransfers', orgId] as const,
  projects: (scope: string | null, id: string | null) =>
    ['tenancy', 'projects', scope, id] as const,
  project: (id: string) => ['tenancy', 'project', id] as const,
  projectMembers: (projectId: string) =>
    ['tenancy', 'projectMembers', projectId] as const,
  invites: (state?: string) => ['tenancy', 'invites', state] as const,
}

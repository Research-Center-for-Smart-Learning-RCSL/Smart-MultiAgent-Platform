export interface UserSummary {
  id: string
  email: string
  status: 'active' | 'pending' | 'banned' | 'deleted'
  email_verified: boolean
  created_at: string
}

export interface UserDetail {
  id: string
  email: string
  status: 'active' | 'pending' | 'banned' | 'deleted'
  email_verified: boolean
  is_admin: boolean
  banned_reason: string | null
  banned_at: string | null
  deleted_at: string | null
  last_login_at: string | null
  created_at: string
  org_ids: string[]
  project_ids: string[]
}

export interface AdminEntry {
  user_id: string
  promoted_by_user_id: string | null
  promoted_at: string
}

export interface AuditEntry {
  id: number
  actor_user_id: string | null
  actor_ip: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  metadata: Record<string, unknown>
  session_id: string | null
  request_id: string | null
  created_at: string
}

export interface AuditPage {
  items: AuditEntry[]
  next_cursor: number | null
}

export interface AuditFilter {
  actor_user_id?: string
  resource_type?: string
  resource_id?: string
  action?: string
  from?: string
  to?: string
  ip_prefix?: string
  session_id?: string
  request_id?: string
  cursor?: number
  limit?: number
}

export interface OrgSummary {
  id: string
  name: string
  creator_user_id: string
  deleted_at: string | null
  created_at: string
}

export interface ProjectSummary {
  id: string
  name: string
  owner_user_id: string | null
  owner_org_id: string | null
  deleted_at: string | null
  created_at: string
}

export interface RateLimitPolicy {
  key: string
  window_sec: number
  max_count: number
  scope: string
  updated_at: string
}

export interface Metrics {
  total_users: number
  total_orgs: number
  total_projects: number
  total_audit_entries: number
}

export interface ImpersonateResult {
  session_id: string
  access_token: string
}

export interface IpBan {
  id: string
  cidr: string
  reason: string
  created_by_user_id: string
  banned_at: string
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

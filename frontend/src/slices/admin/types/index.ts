export interface UserSummary {
  id: string
  email: string
  display_name: string | null
  status: 'active' | 'pending' | 'banned' | 'deleted'
  email_verified: boolean
  created_at: string
}

export interface UserDetail {
  id: string
  email: string
  display_name: string | null
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

/** The two single-use links an Admin hands over to activate a provisioned
 *  account (R6.18). Both are bearer credentials and the set-password one is
 *  equivalent to the password itself, so they are returned by the two mint
 *  endpoints only and must never be logged or persisted client-side. */
export interface ActivationLinks {
  set_password_url: string
  verify_email_url: string
  set_password_expires_at: string
  verify_email_expires_at: string
}

export interface ProvisionedUser {
  user: UserSummary
  activation_links: ActivationLinks
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

/** One activity type, platform-wide, for the admin governance view (R30.31).
 *
 *  `validator_config` may hold answer keys and is admin-confidential (R30.25).
 *  This type is deliberately scoped to the admin slice: do not re-export it for a
 *  non-admin surface to reuse. */
export interface AdminActivityTypeRow {
  id: string
  /** Both null for a platform-scoped type: an installed example has no owning
   *  project ([R30.02]). */
  project_id: string | null
  project_name: string | null
  scope: ActivityTypeScope
  key: string
  name: string
  validator_kind: string
  validator_config: Record<string, unknown>
  expose_payload_to_agent: boolean
  echo_includes_content: boolean
  retention_days: number | null
  version: number
  created_at: string
}

/** Who owns an activity type ([R30.02]). A `platform` row is a shipped example an
 *  admin installed; it has no project and is read-only to Project Owners. */
export type ActivityTypeScope = 'project' | 'platform'

/** One activity type a shipped course would install, and whether it already is. */
export interface AdminCatalogueTypeRow {
  key: string
  name: string
  expose_payload_to_agent: boolean
  echo_includes_content: boolean
  retention_days: number | null
  installed_type_id: string | null
}

/** One shipped course in the example catalogue ([R30.32]). */
export interface AdminActivityExample {
  course_key: string
  title: string
  source: string
  activity_types: AdminCatalogueTypeRow[]
  fully_installed: boolean
}

/** What one install changed. Both lists rather than a count: an install is
 *  idempotent, so "nothing created" is a normal outcome the admin needs explained. */
export interface AdminInstallReport {
  course_key: string
  created: string[]
  already_present: string[]
}

/** The four fields a platform admin may edit on an installed example ([R30.23]).
 *  `key`, `payload_schema` and `validator_config` are absent by design. */
export interface AdminPlatformActivityTypeInput {
  name: string
  retention_days: number | null
  expose_payload_to_agent: boolean
  echo_includes_content: boolean
}

/** One currently-active activation, platform-wide. Names are null when the room
 *  or type has since been removed; the ids are always present. */
export interface AdminActivityActivationRow {
  id: string
  chatroom_id: string
  chatroom_name: string | null
  activity_type_id: string
  activity_type_key: string | null
  activity_type_name: string | null
  started_by_user_id: string
  created_at: string
}

/** Platform activity governance policy (R30.29).
 *
 *  `version` 0 means no policy has ever been saved: the client is creating, and
 *  must not send If-Match. */
export interface ActivityPolicy {
  expose_payload_to_agent_default: boolean
  expose_payload_to_agent_locked: boolean
  echo_includes_content_default: boolean
  echo_includes_content_locked: boolean
  retention_days_default: number | null
  retention_days_max: number | null
  version: number
  updated_at: string | null
  updated_by_user_id: string | null
}

export type ActivityPolicyInput = Omit<
  ActivityPolicy,
  'version' | 'updated_at' | 'updated_by_user_id'
>

/** What a candidate policy would block.
 *
 *  `violating_activations` counts activities running right now whose type the
 *  candidate would refuse. They keep running: enforcement is at authoring and at
 *  activation start, so tightening does not stop a class already under way.
 *  `approximate` means a server scan hit its bound, so these are floors. */
export interface ActivityPolicyImpact {
  violating_types: number
  violating_activations: number
  approximate: boolean
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
  created_by_user_id: string | null
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

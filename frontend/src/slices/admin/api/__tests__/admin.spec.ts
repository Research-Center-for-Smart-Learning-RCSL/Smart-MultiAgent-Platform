import { describe, expect, it } from 'vitest'
import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { adminApi } from '../admin'

// Request-level characterization of the admin api wire contract, pinned as
// docs/tasks/2026-07-12-generated-client-wrap-admin converts the 25 adminApi methods from
// @shared/transport's `http` to the generated AdminService (+ GraphragAdminService). The
// methods already returned bare bodies (conversation pattern), so this is signature-
// preserving; the guard is: verb/path/body must not move, the AuditFilter snake_case query
// keys must survive the camelCase round-trip (and unset filters must not ship), the six-type
// restore path must pass through, and the resetGraphrag route must hit graphrag-admin.

const userSummaryOut = {
  id: 'u_1',
  email: 'a@x.io',
  display_name: 'Ada',
  status: 'active',
  email_verified: true,
  created_at: 't',
}
const userDetailOut = {
  ...userSummaryOut,
  is_admin: false,
  banned_reason: null,
  banned_at: null,
  deleted_at: null,
  last_login_at: null,
  org_ids: [],
  project_ids: [],
}
const adminEntryOut = { user_id: 'u_1', promoted_by_user_id: null, promoted_at: 't' }
const orgSummaryOut = { id: 'o_1', name: 'Acme', creator_user_id: 'u_1', deleted_at: null, created_at: 't' }
const projectSummaryOut = {
  id: 'p_1',
  name: 'Proj',
  owner_user_id: 'u_1',
  owner_org_id: null,
  deleted_at: null,
  created_at: 't',
}
const metricsOut = { total_users: 1, total_orgs: 2, total_projects: 3, total_audit_entries: 4 }
const impersonateOut = { session_id: 's_1', access_token: 'at_1' }
const ipBanOut = { id: 'b_1', cidr: '10.0.0.0/8', reason: 'abuse', created_by_user_id: null, banned_at: 't' }
const rateLimitOut = { key: 'login', window_sec: 60, max_count: 5, scope: 'ip', updated_at: 't' }
const auditPageOut = {
  items: [
    {
      id: 1,
      actor_user_id: 'u_1',
      actor_ip: '10.0.0.1',
      action: 'login',
      resource_type: null,
      resource_id: null,
      metadata: {},
      session_id: null,
      request_id: null,
      created_at: 't',
    },
  ],
  next_cursor: null,
}

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    on('get', '/api/admin/users', [userSummaryOut]),
    on('get', '/api/admin/users/:uid', userDetailOut),
    on('post', '/api/admin/users/:uid/ban', null, 204),
    on('post', '/api/admin/users/:uid/unban', null, 204),
    on('post', '/api/admin/users/:uid/delete', null, 204),
    on('post', '/api/admin/users/:uid/hard-delete', null, 204),
    on('post', '/api/admin/users/:uid/impersonate', impersonateOut),
    on('post', '/api/admin/users/:uid/end-impersonate', null, 204),
    on('get', '/api/admin/admins', [adminEntryOut]),
    on('post', '/api/admin/admins', adminEntryOut, 201),
    on('delete', '/api/admin/admins/:uid', null, 204),
    on('get', '/api/admin/orgs', [orgSummaryOut]),
    on('post', '/api/admin/orgs/:oid/force-delete', null, 204),
    on('post', '/api/admin/orgs/:oid/force-transfer-original-creator', { ok: true }),
    on('get', '/api/admin/projects', [projectSummaryOut]),
    on('get', '/api/admin/audit', auditPageOut),
    on('post', '/api/admin/audit/export', { url: 'https://x/export.csv', job_id: 'j_1' }),
    on('post', '/api/admin/restore/:type/:id', { restored: true }),
    on('get', '/api/admin/metrics', metricsOut),
    on('get', '/api/admin/rate-limits', [rateLimitOut]),
    on('patch', '/api/admin/rate-limits/:key', rateLimitOut),
    on('post', '/api/admin/graphrag/:configId/reset', { id: 'cfg_1' }),
    on('get', '/api/admin/ip-bans', [ipBanOut]),
    on('post', '/api/admin/ip-bans', ipBanOut, 201),
    on('delete', '/api/admin/ip-bans/:bid', null, 204),
  )
  return cap
}

describe('admin api wire contract', () => {
  // ---- users ----
  it('listUsers GETs /admin/users and resolves the bare array', async () => {
    const cap = captureAll()
    const rows = await adminApi.listUsers({ q: 'ada', status: 'active' })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/users' })
    expect(cap.value?.query).toMatchObject({ q: 'ada', status: 'active' })
    expect(rows[0]).toMatchObject({ id: 'u_1', status: 'active' })
  })

  it('getUser GETs the user detail', async () => {
    const cap = captureAll()
    const u = await adminApi.getUser('u_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/users/u_1' })
    expect(u).toMatchObject({ id: 'u_1', is_admin: false, org_ids: [] })
  })

  it('banUser POSTs { reason }', async () => {
    const cap = captureAll()
    await adminApi.banUser('u_1', 'spam')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/admin/users/u_1/ban',
      body: { reason: 'spam' },
    })
  })

  it('unbanUser / softDeleteUser / hardDeleteUser / endImpersonate POST their routes', async () => {
    const cap = captureAll()
    await adminApi.unbanUser('u_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/users/u_1/unban' })
    await adminApi.softDeleteUser('u_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/users/u_1/delete' })
    await adminApi.hardDeleteUser('u_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/users/u_1/hard-delete' })
    await adminApi.endImpersonate('u_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/users/u_1/end-impersonate' })
  })

  it('impersonate POSTs and resolves { session_id, access_token }', async () => {
    const cap = captureAll()
    const res = await adminApi.impersonate('u_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/users/u_1/impersonate' })
    expect(res).toEqual({ session_id: 's_1', access_token: 'at_1' })
  })

  // ---- admins ----
  it('listAdmins GETs /admin/admins', async () => {
    const cap = captureAll()
    await adminApi.listAdmins()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/admins' })
  })

  it('promoteAdmin POSTs { user_id }', async () => {
    const cap = captureAll()
    await adminApi.promoteAdmin('u_2')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/admin/admins',
      body: { user_id: 'u_2' },
    })
  })

  it('demoteAdmin DELETEs the admin', async () => {
    const cap = captureAll()
    await adminApi.demoteAdmin('u_2')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/admin/admins/u_2' })
  })

  // ---- orgs / projects ----
  it('listOrgs / listProjects GET their routes', async () => {
    const cap = captureAll()
    await adminApi.listOrgs()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/orgs' })
    await adminApi.listProjects()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/projects' })
  })

  it('forceDeleteOrg POSTs force-delete', async () => {
    const cap = captureAll()
    await adminApi.forceDeleteOrg('o_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/orgs/o_1/force-delete' })
  })

  it('forceTransferOC POSTs { target_user_id }', async () => {
    const cap = captureAll()
    await adminApi.forceTransferOC('o_1', 'u_2')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/admin/orgs/o_1/force-transfer-original-creator',
      body: { target_user_id: 'u_2' },
    })
  })

  // ---- audit (query translation) ----
  it('queryAudit maps AuditFilter snake_case fields to the query and omits unset ones', async () => {
    const cap = captureAll()
    const page = await adminApi.queryAudit({
      actor_user_id: 'u_1',
      ip_prefix: '10.0.0.0/8',
      action: 'login',
    })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/audit' })
    expect(cap.value?.query).toMatchObject({
      actor_user_id: 'u_1',
      ip_prefix: '10.0.0.0/8',
      action: 'login',
    })
    // Unset filters coalesce to null and are dropped from the query string.
    expect(cap.value?.query.resource_type).toBeUndefined()
    expect(cap.value?.query.session_id).toBeUndefined()
    // The generated client injects its declared default limit when the caller omits it
    // (accepted deviation D-1). Pinned here so a regen that changes the default is reviewed.
    expect(cap.value?.query.limit).toBe('50')
    expect(page).toMatchObject({ next_cursor: null })
    expect(page.items[0]).toMatchObject({ id: 1, action: 'login' })
  })

  it('queryAudit forwards a set cursor and limit', async () => {
    const cap = captureAll()
    await adminApi.queryAudit({ cursor: 42, limit: 10 })
    expect(cap.value?.query).toMatchObject({ cursor: '42', limit: '10' })
  })

  it('exportAudit POSTs to the export route with the filters and resolves { url }', async () => {
    const cap = captureAll()
    const res = await adminApi.exportAudit({ from: '2026-01-01', to: '2026-02-01' })
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/audit/export' })
    expect(cap.value?.query).toMatchObject({ from: '2026-01-01', to: '2026-02-01' })
    expect(res.url).toBe('https://x/export.csv')
  })

  // ---- restore (six-type path via the boundary cast) ----
  it('restoreResource POSTs /admin/restore/{type}/{id}, passing a type beyond the OpenAPI enum', async () => {
    const cap = captureAll()
    const res = await adminApi.restoreResource('agent', 'a_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/restore/agent/a_1' })
    expect(res).toEqual({ restored: true })
  })

  // ---- metrics / rate limits ----
  it('getMetrics GETs /admin/metrics', async () => {
    const cap = captureAll()
    const m = await adminApi.getMetrics()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/metrics' })
    expect(m).toMatchObject({ total_users: 1 })
  })

  it('listRateLimits GETs /admin/rate-limits', async () => {
    const cap = captureAll()
    await adminApi.listRateLimits()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/rate-limits' })
  })

  it('patchRateLimit PATCHes the policy body', async () => {
    const cap = captureAll()
    await adminApi.patchRateLimit('login', { max_count: 10, window_sec: 120 })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/admin/rate-limits/login',
      body: { max_count: 10, window_sec: 120 },
    })
  })

  // ---- graphrag reset (different generated service, same route) ----
  it('resetGraphrag POSTs to the graphrag-admin reset route', async () => {
    const cap = captureAll()
    await adminApi.resetGraphrag('cfg_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/admin/graphrag/cfg_1/reset' })
  })

  // ---- ip bans ----
  it('listIpBans GETs /admin/ip-bans and tolerates a null created_by_user_id', async () => {
    const cap = captureAll()
    const bans = await adminApi.listIpBans()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/ip-bans' })
    expect(bans[0]).toMatchObject({ id: 'b_1', created_by_user_id: null })
  })

  it('createIpBan POSTs { cidr, reason }', async () => {
    const cap = captureAll()
    await adminApi.createIpBan('10.0.0.0/8', 'abuse')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/admin/ip-bans',
      body: { cidr: '10.0.0.0/8', reason: 'abuse' },
    })
  })

  it('deleteIpBan DELETEs the ban', async () => {
    const cap = captureAll()
    await adminApi.deleteIpBan('b_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/admin/ip-bans/b_1' })
  })
})

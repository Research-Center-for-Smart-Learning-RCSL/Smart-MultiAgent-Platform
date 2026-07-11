import { describe, expect, it } from 'vitest'
import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { orgsApi, projectsApi, invitesApi } from '..'

// Request-level characterization of the tenancy api wire contract, pinned as
// docs/tasks/2026-07-12-generated-client-wrap-tenancy converts orgs/projects/invites
// from @shared/transport's `http` to the generated services. This is the agent-groups
// pattern (the methods now resolve the bare body), so the guard is: verb/path/params/body
// and the two If-Match preconditions must not move, the projects.list scope/id pair must
// stay both-or-neither, and the three invite endpoints must map to their distinct shapes.

const orgOut = {
  id: 'org_1',
  name: 'Acme',
  creator_user_id: 'u_1',
  default_project_id: null,
  version: 3,
  created_at: 't',
  deleted_at: null,
}
const orgMemberOut = {
  user_id: 'u_1',
  email: 'a@x.io',
  role: 'owner',
  is_original_creator: true,
  joined_at: 't',
}
const quotasOut = {
  users: 1,
  projects: 2,
  chatrooms: 3,
  agents: 4,
  workflows: 5,
  computed_at: null,
  advisory_targets: {},
}
const transferOut = {
  id: 'tr_1',
  org_id: 'org_1',
  initiator_user_id: 'u_1',
  target_user_id: 'u_2',
  state: 'pending',
  created_at: 't',
  expires_at: 't',
}
const projectOut = {
  id: 'proj_1',
  name: 'Proj',
  owner_type: 'org',
  owner_id: 'org_1',
  created_by_user_id: 'u_1',
  version: 2,
  created_at: 't',
  deleted_at: null,
}
const projectMemberOut = {
  user_id: 'u_1',
  email: 'a@x.io',
  role: 'member',
  joined_at: 't',
}
// The invites-scoped InviteOut is the only one carrying created_at + scope_name.
const inboxInviteOut = {
  id: 'inv_1',
  scope_type: 'org',
  scope_id: 'org_1',
  scope_name: 'Acme',
  invitee_email: 'b@x.io',
  role: 'member',
  state: 'pending',
  created_at: 't',
  expires_at: 't',
}
const orgInviteOut = {
  id: 'inv_2',
  scope_id: 'org_1',
  invitee_email: 'b@x.io',
  role: 'member',
  scope_type: 'org',
  state: 'pending',
  expires_at: 't',
}

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    // orgs
    on('get', '/api/orgs', [orgOut]),
    on('post', '/api/orgs', orgOut, 201),
    on('get', '/api/orgs/:oid', orgOut),
    on('patch', '/api/orgs/:oid', orgOut),
    on('delete', '/api/orgs/:oid', null, 204),
    on('post', '/api/orgs/:oid/restore', null, 204),
    on('get', '/api/orgs/:oid/quotas', quotasOut),
    on('get', '/api/orgs/:oid/members', [orgMemberOut]),
    on('delete', '/api/orgs/:oid/members/:uid', null, 204),
    on('patch', '/api/orgs/:oid/members/:uid', { status: 'ok' }),
    on('post', '/api/orgs/:oid/invites', orgInviteOut, 201),
    on('post', '/api/orgs/:oid/original-creator-transfers', transferOut, 201),
    on('get', '/api/orgs/:oid/original-creator-transfers', [transferOut]),
    on('post', '/api/orgs/:oid/original-creator-transfers/:tid/accept', transferOut),
    on('delete', '/api/orgs/:oid/original-creator-transfers/:tid', null, 204),
    on('post', '/api/orgs/:oid/original-creator-transfers/:tid/reject', null, 204),
    // projects
    on('get', '/api/projects', [projectOut]),
    on('post', '/api/projects', projectOut, 201),
    on('get', '/api/projects/:pid', projectOut),
    on('delete', '/api/projects/:pid', null, 204),
    on('post', '/api/projects/:pid/restore', null, 204),
    on('patch', '/api/projects/:pid', projectOut),
    on('get', '/api/projects/:pid/members', [projectMemberOut]),
    on('delete', '/api/projects/:pid/members/:uid', null, 204),
    on('patch', '/api/projects/:pid/members/:uid', { status: 'ok' }),
    on('post', '/api/projects/:pid/invites', { invite_id: 'inv_3' }, 201),
    // invites
    on('get', '/api/invites', [inboxInviteOut]),
    on('post', '/api/invites/accept-by-token', inboxInviteOut),
    on('post', '/api/invites/:iid/accept', inboxInviteOut),
    on('post', '/api/invites/:iid/reject', inboxInviteOut),
  )
  return cap
}

describe('tenancy api wire contract', () => {
  // ---- orgs ----
  it('orgsApi.list GETs /orgs and resolves the bare array', async () => {
    const cap = captureAll()
    const rows = await orgsApi.list()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orgs' })
    expect(rows[0]).toMatchObject({ id: 'org_1', name: 'Acme' })
  })

  it('orgsApi.create POSTs { name }', async () => {
    const cap = captureAll()
    await orgsApi.create('Acme')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/orgs', body: { name: 'Acme' } })
  })

  it('orgsApi.get GETs the org', async () => {
    const cap = captureAll()
    const org = await orgsApi.get('org_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orgs/org_1' })
    expect(org).toMatchObject({ id: 'org_1', version: 3 })
  })

  it('orgsApi.rename PATCHes { name } with If-Match: String(version)', async () => {
    const cap = captureAll()
    await orgsApi.rename('org_1', 'Renamed', 3)
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/orgs/org_1',
      ifMatch: '3',
      body: { name: 'Renamed' },
    })
  })

  it('orgsApi.remove DELETEs the org', async () => {
    const cap = captureAll()
    await orgsApi.remove('org_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/orgs/org_1' })
  })

  it('orgsApi.restore POSTs to restore and resolves void', async () => {
    const cap = captureAll()
    const res = await orgsApi.restore('org_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/orgs/org_1/restore' })
    expect(res).toBeUndefined()
  })

  it('orgsApi.quotas GETs the quotas', async () => {
    const cap = captureAll()
    const q = await orgsApi.quotas('org_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orgs/org_1/quotas' })
    expect(q).toMatchObject({ users: 1, advisory_targets: {} })
  })

  it('orgsApi.listMembers GETs the members', async () => {
    const cap = captureAll()
    const members = await orgsApi.listMembers('org_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orgs/org_1/members' })
    expect(members[0]).toMatchObject({ user_id: 'u_1', is_original_creator: true })
  })

  it('orgsApi.removeMember DELETEs the member', async () => {
    const cap = captureAll()
    await orgsApi.removeMember('org_1', 'u_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/orgs/org_1/members/u_1' })
  })

  it('orgsApi.setRole PATCHes { role }', async () => {
    const cap = captureAll()
    await orgsApi.setRole('org_1', 'u_1', 'owner')
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/orgs/org_1/members/u_1',
      body: { role: 'owner' },
    })
  })

  it('orgsApi.invite POSTs { email, role } to the org invites route', async () => {
    const cap = captureAll()
    await orgsApi.invite('org_1', 'b@x.io', 'member')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/org_1/invites',
      body: { email: 'b@x.io', role: 'member' },
    })
  })

  it('orgsApi.initiateTransfer POSTs { target_user_id }', async () => {
    const cap = captureAll()
    const tr = await orgsApi.initiateTransfer('org_1', 'u_2')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/org_1/original-creator-transfers',
      body: { target_user_id: 'u_2' },
    })
    expect(tr).toMatchObject({ id: 'tr_1', state: 'pending' })
  })

  it('orgsApi.listTransfers GETs the transfers', async () => {
    const cap = captureAll()
    const rows = await orgsApi.listTransfers('org_1')
    expect(cap.value).toMatchObject({
      method: 'GET',
      path: '/api/orgs/org_1/original-creator-transfers',
    })
    expect(rows[0]).toMatchObject({ id: 'tr_1' })
  })

  it('orgsApi.acceptTransfer POSTs to accept', async () => {
    const cap = captureAll()
    await orgsApi.acceptTransfer('org_1', 'tr_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/org_1/original-creator-transfers/tr_1/accept',
    })
  })

  it('orgsApi.cancelTransfer DELETEs the transfer', async () => {
    const cap = captureAll()
    await orgsApi.cancelTransfer('org_1', 'tr_1')
    expect(cap.value).toMatchObject({
      method: 'DELETE',
      path: '/api/orgs/org_1/original-creator-transfers/tr_1',
    })
  })

  it('orgsApi.rejectTransfer POSTs to reject', async () => {
    const cap = captureAll()
    await orgsApi.rejectTransfer('org_1', 'tr_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/org_1/original-creator-transfers/tr_1/reject',
    })
  })

  // ---- projects ----
  it('projectsApi.list sends scope+id together when both are given', async () => {
    const cap = captureAll()
    await projectsApi.list('org', 'org_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects' })
    expect(cap.value?.query).toMatchObject({ scope: 'org', id: 'org_1' })
  })

  it('projectsApi.list omits scope and id when either is missing', async () => {
    const cap = captureAll()
    await projectsApi.list('org')
    expect(cap.value?.query.scope).toBeUndefined()
    expect(cap.value?.query.id).toBeUndefined()
  })

  it('projectsApi.create POSTs { owner_type, owner_id, name }', async () => {
    const cap = captureAll()
    await projectsApi.create('org', 'org_1', 'Proj')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects',
      body: { owner_type: 'org', owner_id: 'org_1', name: 'Proj' },
    })
  })

  it('projectsApi.get GETs the project', async () => {
    const cap = captureAll()
    await projectsApi.get('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1' })
  })

  it('projectsApi.remove DELETEs the project', async () => {
    const cap = captureAll()
    await projectsApi.remove('proj_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/projects/proj_1' })
  })

  it('projectsApi.restore POSTs to restore and resolves void', async () => {
    const cap = captureAll()
    const res = await projectsApi.restore('proj_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/projects/proj_1/restore' })
    expect(res).toBeUndefined()
  })

  it('projectsApi.rename PATCHes { name } with If-Match: String(version)', async () => {
    const cap = captureAll()
    await projectsApi.rename('proj_1', 'Renamed', 2)
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/projects/proj_1',
      ifMatch: '2',
      body: { name: 'Renamed' },
    })
  })

  it('projectsApi.listMembers GETs the members', async () => {
    const cap = captureAll()
    const members = await projectsApi.listMembers('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/members' })
    expect(members[0]).toMatchObject({ user_id: 'u_1', role: 'member' })
  })

  it('projectsApi.removeMember DELETEs the member', async () => {
    const cap = captureAll()
    await projectsApi.removeMember('proj_1', 'u_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/projects/proj_1/members/u_1' })
  })

  it('projectsApi.setRole PATCHes { role }', async () => {
    const cap = captureAll()
    await projectsApi.setRole('proj_1', 'u_1', 'owner')
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/projects/proj_1/members/u_1',
      body: { role: 'owner' },
    })
  })

  it('projectsApi.invite POSTs { email, role } to the project invites route', async () => {
    const cap = captureAll()
    await projectsApi.invite('proj_1', 'b@x.io', 'member')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/invites',
      body: { email: 'b@x.io', role: 'member' },
    })
  })

  // ---- invites ----
  it('invitesApi.list GETs /invites with the state query and resolves the inbox shape', async () => {
    const cap = captureAll()
    const rows = await invitesApi.list('pending')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/invites' })
    expect(cap.value?.query).toMatchObject({ state: 'pending' })
    // The inbox invite carries created_at + scope_name (the fields the Invite type needs).
    expect(rows[0]).toMatchObject({ id: 'inv_1', created_at: 't', scope_name: 'Acme' })
  })

  it('invitesApi.list defaults the state to pending', async () => {
    const cap = captureAll()
    await invitesApi.list()
    expect(cap.value?.query).toMatchObject({ state: 'pending' })
  })

  it('invitesApi.accept POSTs to accept', async () => {
    const cap = captureAll()
    await invitesApi.accept('inv_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/invites/inv_1/accept' })
  })

  it('invitesApi.acceptByToken POSTs { token }', async () => {
    const cap = captureAll()
    await invitesApi.acceptByToken('tok_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/invites/accept-by-token',
      body: { token: 'tok_1' },
    })
  })

  it('invitesApi.reject POSTs to reject', async () => {
    const cap = captureAll()
    await invitesApi.reject('inv_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/invites/inv_1/reject' })
  })
})

import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import { agentGroupsApi } from '..'

// Request-level characterization of the agentGroupsApi wire contract, pinned
// before docs/tasks/2026-07-10-generated-client-wrap-agent-groups converts the
// slice from @shared/transport's `http` to the generated AgentGroupsService.
// These assert the OUTBOUND request (verb/path/body) only — deliberately not
// the return shape, which changes from AxiosResponse<T> to the bare body (Q-3).
// So the file must pass unmodified against both the pre- and post-conversion
// code. `list`'s query is intentionally not asserted: the generated client adds
// an inert `limit=100` (backend default is also 100), a documented wire delta.

interface Captured {
  method: string
  path: string
  body: unknown
}

// Register a one-shot capturing handler for every endpoint the slice touches,
// returning a benign success so each api call resolves. Returns a holder whose
// `.value` is filled with the captured request once a matching call fires.
function captureAll(): { value: Captured | null } {
  const holder: { value: Captured | null } = { value: null }
  const record = async (request: Request): Promise<void> => {
    let body: unknown = undefined
    if (request.method !== 'GET' && request.method !== 'DELETE') {
      body = await request.clone().json().catch(() => undefined)
    }
    holder.value = { method: request.method, path: new URL(request.url).pathname, body }
  }
  const ok = (json: unknown, status = 200): HttpResponse =>
    json === null ? new HttpResponse(null, { status }) : HttpResponse.json(json, { status })

  server.use(
    mswHttp.get('/api/projects/:projectId/agent-groups', async ({ request }) => {
      await record(request)
      return ok([])
    }),
    mswHttp.post('/api/projects/:projectId/agent-groups', async ({ request }) => {
      await record(request)
      return ok({ id: 'g_1', project_id: 'proj_1', name: 'x', concept_map_enabled: false, created_at: 't' }, 201)
    }),
    mswHttp.get('/api/agent-groups/:groupId', async ({ request }) => {
      await record(request)
      return ok({ id: 'g_1', project_id: 'proj_1', name: 'x', concept_map_enabled: false, created_at: 't' })
    }),
    mswHttp.patch('/api/agent-groups/:groupId', async ({ request }) => {
      await record(request)
      return ok({ id: 'g_1', project_id: 'proj_1', name: 'x', concept_map_enabled: false, created_at: 't' })
    }),
    mswHttp.delete('/api/agent-groups/:groupId', async ({ request }) => {
      await record(request)
      return ok(null, 204)
    }),
    mswHttp.get('/api/agent-groups/:groupId/members', async ({ request }) => {
      await record(request)
      return ok({ members: [] })
    }),
    mswHttp.post('/api/agent-groups/:groupId/members', async ({ request }) => {
      await record(request)
      return ok({ members: ['a_1'] }, 201)
    }),
    mswHttp.delete('/api/agent-groups/:groupId/members/:agentId', async ({ request }) => {
      await record(request)
      return ok(null, 204)
    }),
    mswHttp.put('/api/agent-groups/:groupId/concept-map-enabled', async ({ request }) => {
      await record(request)
      return ok({ group_id: 'g_1', concept_map_enabled: true })
    }),
  )
  return holder
}

describe('agentGroupsApi wire contract', () => {
  it('list GETs the project agent-groups collection', async () => {
    const cap = captureAll()
    await agentGroupsApi.list('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/agent-groups' })
  })

  it('create POSTs { name } to the project collection', async () => {
    const cap = captureAll()
    await agentGroupsApi.create('proj_1', { name: 'Research Team' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/agent-groups',
      body: { name: 'Research Team' },
    })
  })

  it('get GETs a single group', async () => {
    const cap = captureAll()
    await agentGroupsApi.get('g_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/agent-groups/g_1' })
  })

  it('rename PATCHes { name } to the group', async () => {
    const cap = captureAll()
    await agentGroupsApi.rename('g_1', { name: 'New Name' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/agent-groups/g_1',
      body: { name: 'New Name' },
    })
  })

  it('remove DELETEs the group', async () => {
    const cap = captureAll()
    await agentGroupsApi.remove('g_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/agent-groups/g_1' })
  })

  it('listMembers GETs the group members', async () => {
    const cap = captureAll()
    await agentGroupsApi.listMembers('g_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/agent-groups/g_1/members' })
  })

  it('addMember POSTs { agent_id } to the group members', async () => {
    const cap = captureAll()
    await agentGroupsApi.addMember('g_1', 'a_7')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/agent-groups/g_1/members',
      body: { agent_id: 'a_7' },
    })
  })

  it('removeMember DELETEs a single member', async () => {
    const cap = captureAll()
    await agentGroupsApi.removeMember('g_1', 'a_7')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/agent-groups/g_1/members/a_7' })
  })

  it('setConceptMapEnabled PUTs { enabled } to the concept-map-enabled toggle', async () => {
    const cap = captureAll()
    await agentGroupsApi.setConceptMapEnabled('g_1', true)
    expect(cap.value).toMatchObject({
      method: 'PUT',
      path: '/api/agent-groups/g_1/concept-map-enabled',
      body: { enabled: true },
    })
  })
})

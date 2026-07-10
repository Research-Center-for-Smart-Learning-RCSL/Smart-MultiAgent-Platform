import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import { keyGroupsApi, keysApi, projectKeysApi, searchKeysApi } from '..'

// Request-level characterization of the keys api wire contract, pinned as
// docs/tasks/2026-07-10-generated-client-wrap-keys converts the four modules from
// @shared/transport's `http` to the generated services. Assertions cover the
// outbound request (verb / path / query / body) and the one response bridge
// (toKeyGroup defaulting a GroupOut without member_count/providers). Bodies matter
// here because this is a provider-keys surface: the upload `secret` must reach the
// same endpoint unchanged.

interface Captured {
  method: string
  path: string
  query: Record<string, string>
  body: unknown
}

const rotationOut = {
  rotate_on_error_codes: [429],
  rotate_on_token_quota: true,
  retry_on_error: true,
  retry_initial_delay_ms: 100,
  retry_multiplier: 2,
  retry_max_delay_ms: 1000,
  retry_max: 3,
  retry_jitter_pct: 10,
}
const limitsOut = {
  max_input_tokens_per_hour: null,
  max_output_tokens_per_hour: null,
  max_requests_per_hour: null,
}
const keyOut = {
  id: 'k_1',
  provider: 'claude',
  name: 'Key',
  masked_preview: 'sk-***abc',
  test_status: 'ok',
  test_error: null,
  last_test_at: null,
  created_at: 't',
}
const keyListOut = { ...keyOut, project_count: 2 }
const memberOut = { key_id: 'k_1', priority: 1, rotation: rotationOut, limits: limitsOut }
const groupOutFull = {
  id: 'kg_1',
  project_id: 'proj_1',
  name: 'Group',
  created_at: 't',
  member_count: 3,
  providers: ['claude'],
}
// Deliberately omits member_count/providers to exercise the toKeyGroup defaults.
const groupOutBare = { id: 'kg_2', project_id: 'proj_1', name: 'Bare', created_at: 't' }
const searchKeyOut = {
  id: 'sk_1',
  project_id: 'proj_1',
  provider: 'brave',
  masked_preview: 'B***xyz',
  test_status: 'ok',
  test_error: null,
  last_test_at: null,
  is_active: true,
  config: {},
  created_at: 't',
}

function captureAll(overrides?: {
  groups?: unknown
  groupDetail?: unknown
}): { value: Captured | null } {
  const holder: { value: Captured | null } = { value: null }
  const record = async (request: Request): Promise<void> => {
    const url = new URL(request.url)
    let body: unknown = undefined
    if (request.method !== 'GET' && request.method !== 'DELETE') {
      body = await request.clone().json().catch(() => undefined)
    }
    holder.value = {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      body,
    }
  }
  const ok = (json: unknown, status = 200): HttpResponse =>
    json === null ? new HttpResponse(null, { status }) : HttpResponse.json(json, { status })
  const on = (
    method: 'get' | 'post' | 'patch' | 'delete',
    path: string,
    json: unknown,
    status = 200,
  ) =>
    mswHttp[method](path, async ({ request }) => {
      await record(request)
      return ok(json, status)
    })

  server.use(
    // keysApi
    on('get', '/api/keys', [keyListOut]),
    on('post', '/api/keys', keyOut, 201),
    on('get', '/api/keys/:id', keyOut),
    on('post', '/api/keys/:id/retest', keyOut),
    on('delete', '/api/keys/:id', null, 204),
    on('get', '/api/keys/:id/projects', [
      { project_id: 'p_1', project_name: 'P', carried_at: 't', group_count: 1, agent_count: 2 },
    ]),
    // keyGroupsApi
    on('get', '/api/projects/:pid/key-groups', overrides?.groups ?? [groupOutFull]),
    on('post', '/api/projects/:pid/key-groups', groupOutFull, 201),
    on('get', '/api/key-groups/:gid', overrides?.groupDetail ?? { group: groupOutFull, members: [memberOut] }),
    on('patch', '/api/key-groups/:gid', null, 204),
    on('delete', '/api/key-groups/:gid', null, 204),
    on('post', '/api/key-groups/:gid/keys', memberOut, 201),
    on('patch', '/api/key-groups/:gid/keys/:kid', null, 204),
    on('delete', '/api/key-groups/:gid/keys/:kid', null, 204),
    on('post', '/api/key-groups/:gid/reorder', null, 204),
    // searchKeysApi
    on('get', '/api/projects/:pid/search-keys', [searchKeyOut]),
    on('post', '/api/projects/:pid/search-keys', searchKeyOut, 201),
    on('post', '/api/projects/:pid/search-keys/:id/retest', searchKeyOut),
    on('post', '/api/projects/:pid/search-keys/:id/activate', null, 204),
    on('delete', '/api/projects/:pid/search-keys/:id', null, 204),
    // projectKeysApi
    on('get', '/api/projects/:pid/keys', [keyOut]),
    on('post', '/api/projects/:pid/keys', null, 204),
    on('delete', '/api/projects/:pid/keys/:id', null, 204),
    on('get', '/api/projects/:pid/keys/:id/usage', {
      window: '24h',
      input_tokens: 10,
      output_tokens: 20,
      requests: 5,
      errors: 0,
    }),
  )
  return holder
}

describe('keys api wire contract', () => {
  // ---- keysApi ----
  it('list GETs /keys and returns the keys', async () => {
    const cap = captureAll()
    const keys = await keysApi.list()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/keys' })
    expect(keys[0]).toMatchObject({ id: 'k_1', provider: 'claude', project_count: 2 })
  })

  it('get GETs a single key', async () => {
    const cap = captureAll()
    await keysApi.get('k_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/keys/k_1' })
  })

  it('upload POSTs { provider, name, secret } unchanged', async () => {
    const cap = captureAll()
    await keysApi.upload('openai', 'Prod', 'sk-secret-value')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/keys',
      body: { provider: 'openai', name: 'Prod', secret: 'sk-secret-value' },
    })
  })

  it('retest POSTs to the retest route', async () => {
    const cap = captureAll()
    await keysApi.retest('k_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/keys/k_1/retest' })
  })

  it('remove DELETEs the key', async () => {
    const cap = captureAll()
    await keysApi.remove('k_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/keys/k_1' })
  })

  it('projects GETs the reverse project list', async () => {
    const cap = captureAll()
    const projects = await keysApi.projects('k_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/keys/k_1/projects' })
    expect(projects[0]).toMatchObject({ project_id: 'p_1', group_count: 1, agent_count: 2 })
  })

  // ---- keyGroupsApi ----
  it('listForProject GETs groups and toKeyGroup defaults member_count/providers', async () => {
    const cap = captureAll({ groups: [groupOutBare] })
    const groups = await keyGroupsApi.listForProject('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/key-groups' })
    expect(groups[0]).toMatchObject({ id: 'kg_2', member_count: 0, providers: [] })
  })

  it('create POSTs { name } and returns a populated KeyGroup', async () => {
    const cap = captureAll()
    const group = await keyGroupsApi.create('proj_1', 'New Group')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/key-groups',
      body: { name: 'New Group' },
    })
    expect(group).toMatchObject({ id: 'kg_1', member_count: 3, providers: ['claude'] })
  })

  it('get returns a KeyGroupDetail with bridged group and typed members', async () => {
    const cap = captureAll()
    const detail = await keyGroupsApi.get('kg_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/key-groups/kg_1' })
    expect(detail.group).toMatchObject({ member_count: 3, providers: ['claude'] })
    expect(detail.members[0]).toMatchObject({ key_id: 'k_1', priority: 1 })
    expect(detail.members[0].rotation.retry_max).toBe(3)
  })

  it('rename PATCHes { name }', async () => {
    const cap = captureAll()
    await keyGroupsApi.rename('kg_1', 'Renamed')
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/key-groups/kg_1',
      body: { name: 'Renamed' },
    })
  })

  it('remove DELETEs the group', async () => {
    const cap = captureAll()
    await keyGroupsApi.remove('kg_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/key-groups/kg_1' })
  })

  it('addMember POSTs { key_id }', async () => {
    const cap = captureAll()
    await keyGroupsApi.addMember('kg_1', 'k_9')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/key-groups/kg_1/keys',
      body: { key_id: 'k_9' },
    })
  })

  it('patchMember PATCHes the member patch body', async () => {
    const cap = captureAll()
    await keyGroupsApi.patchMember('kg_1', 'k_9', { priority: 2, retry_max: 5 })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/key-groups/kg_1/keys/k_9',
      body: { priority: 2, retry_max: 5 },
    })
  })

  it('removeMember DELETEs a single member', async () => {
    const cap = captureAll()
    await keyGroupsApi.removeMember('kg_1', 'k_9')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/key-groups/kg_1/keys/k_9' })
  })

  it('reorder POSTs { priorities }', async () => {
    const cap = captureAll()
    await keyGroupsApi.reorder('kg_1', { k_1: 1, k_2: 2 })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/key-groups/kg_1/reorder',
      body: { priorities: { k_1: 1, k_2: 2 } },
    })
  })

  // ---- searchKeysApi ----
  it('list GETs the project search keys', async () => {
    const cap = captureAll()
    const keys = await searchKeysApi.list('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/search-keys' })
    expect(keys[0]).toMatchObject({ id: 'sk_1', provider: 'brave' })
  })

  it('upload POSTs { provider, secret, config } unchanged', async () => {
    const cap = captureAll()
    await searchKeysApi.upload('proj_1', 'serper', 'srp-secret', { search_depth: 'advanced' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/search-keys',
      body: { provider: 'serper', secret: 'srp-secret', config: { search_depth: 'advanced' } },
    })
  })

  it('retest POSTs to the retest route', async () => {
    const cap = captureAll()
    await searchKeysApi.retest('proj_1', 'sk_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/search-keys/sk_1/retest',
    })
  })

  it('activate POSTs to the activate route', async () => {
    const cap = captureAll()
    await searchKeysApi.activate('proj_1', 'sk_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/search-keys/sk_1/activate',
    })
  })

  it('remove DELETEs the search key', async () => {
    const cap = captureAll()
    await searchKeysApi.remove('proj_1', 'sk_1')
    expect(cap.value).toMatchObject({
      method: 'DELETE',
      path: '/api/projects/proj_1/search-keys/sk_1',
    })
  })

  // ---- projectKeysApi ----
  it('listCarried GETs the project-carried keys', async () => {
    const cap = captureAll()
    await projectKeysApi.listCarried('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/keys' })
  })

  it('carry POSTs { key_id }', async () => {
    const cap = captureAll()
    await projectKeysApi.carry('proj_1', 'k_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/keys',
      body: { key_id: 'k_1' },
    })
  })

  it('withdraw DELETEs the carry', async () => {
    const cap = captureAll()
    await projectKeysApi.withdraw('proj_1', 'k_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/projects/proj_1/keys/k_1' })
  })

  it('usage GETs with the window query and returns the aggregate', async () => {
    const cap = captureAll()
    const usage = await projectKeysApi.usage('proj_1', 'k_1', '24h')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/keys/k_1/usage' })
    expect(cap.value?.query).toMatchObject({ window: '24h' })
    expect(usage).toMatchObject({ requests: 5, input_tokens: 10 })
  })
})

import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { promptStudioApi } from '..'

// Request-level characterization of the prompt-studio api wire contract, pinned as
// docs/tasks/2026-07-12-generated-client-wrap-prompt-studio converts the 13 methods from
// @shared/transport's `http` to the generated PromptStudioService (+ ModelCatalogService).
// This is the agent-groups pattern with scope dispatch, so the guard is: each scoped method
// hits the right per-scope endpoint (user -> /me, org -> /orgs/{id}, platform -> /admin), the
// putConfig If-Match is present for a version and absent for null, patchTemplate sends its
// If-Match, the multipart uploadFile carries the file, and the project routes are unchanged.

const configEnvelope = { configured: false, config: null }
const assistantConfig = { scope: 'user', enabled: true, system_prompt: 'x', version: 3 }
const template = {
  id: 't_1',
  scope: 'user',
  name: 'T',
  description: 'd',
  body: 'b',
  position: 0,
  version: 1,
  created_at: 't',
  updated_at: 't',
}
const fileOut = {
  id: 'f_1',
  filename: 'sys.txt',
  size_bytes: 1,
  mime: 'text/plain',
  scan_status: 'pending',
  extracted_chars: 0,
  created_at: 't',
}
const resolved = { available: true, source_scope: 'org', model_id: 'm' }

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    // config (3 scopes)
    on('get', '/api/me/prompt-assistant/config', configEnvelope),
    on('get', '/api/orgs/:oid/prompt-assistant/config', configEnvelope),
    on('get', '/api/admin/prompt-assistant/config', configEnvelope),
    on('put', '/api/me/prompt-assistant/config', assistantConfig),
    on('put', '/api/orgs/:oid/prompt-assistant/config', assistantConfig),
    on('put', '/api/admin/prompt-assistant/config', assistantConfig),
    on('delete', '/api/me/prompt-assistant/config/files/:fid', null, 204),
    on('delete', '/api/orgs/:oid/prompt-assistant/config/files/:fid', null, 204),
    // templates (3 scopes)
    on('get', '/api/me/prompt-templates', [template]),
    on('get', '/api/orgs/:oid/prompt-templates', [template]),
    on('get', '/api/admin/prompt-templates', [template]),
    on('post', '/api/orgs/:oid/prompt-templates', template, 201),
    on('patch', '/api/me/prompt-templates/:tid', template),
    on('delete', '/api/admin/prompt-templates/:tid', null, 204),
    // project-scoped
    on('get', '/api/projects/:pid/prompt-assistant', resolved),
    on('get', '/api/projects/:pid/prompt-templates', [template]),
    on('post', '/api/projects/:pid/prompt-assistant/sessions', { session_id: 'sess_1' }, 201),
    on('post', '/api/prompt-assistant/sessions/:sid/messages', { ok: true }),
    on('get', '/api/model-catalog', { chat: [] }),
  )
  return cap
}

describe('prompt-studio api wire contract', () => {
  // ---- scope dispatch ----
  it('getConfig dispatches user -> /me, org -> /orgs/{id}, platform -> /admin', async () => {
    const cap = captureAll()
    await promptStudioApi.getConfig({ kind: 'user' })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/me/prompt-assistant/config' })
    await promptStudioApi.getConfig({ kind: 'org', orgId: 'o_1' })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orgs/o_1/prompt-assistant/config' })
    await promptStudioApi.getConfig({ kind: 'platform' })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/prompt-assistant/config' })
  })

  it('listTemplates dispatches across the three scopes', async () => {
    const cap = captureAll()
    await promptStudioApi.listTemplates({ kind: 'user' })
    expect(cap.value?.path).toBe('/api/me/prompt-templates')
    await promptStudioApi.listTemplates({ kind: 'org', orgId: 'o_1' })
    expect(cap.value?.path).toBe('/api/orgs/o_1/prompt-templates')
    await promptStudioApi.listTemplates({ kind: 'platform' })
    expect(cap.value?.path).toBe('/api/admin/prompt-templates')
  })

  // ---- If-Match ----
  it('putConfig PUTs the payload with If-Match when a version is given', async () => {
    const cap = captureAll()
    const payload = {
      system_prompt: 'hi',
      key_id: null,
      model_id: null,
      daily_request_limit_per_user: 10,
      enabled: true,
      hide_platform_templates: false,
    }
    await promptStudioApi.putConfig({ kind: 'user' }, 4, payload)
    expect(cap.value).toMatchObject({
      method: 'PUT',
      path: '/api/me/prompt-assistant/config',
      ifMatch: '4',
      body: payload,
    })
  })

  it('putConfig omits If-Match when the version is null', async () => {
    const cap = captureAll()
    await promptStudioApi.putConfig({ kind: 'platform' }, null, {
      system_prompt: 'hi',
      key_id: null,
      model_id: null,
      daily_request_limit_per_user: 10,
      enabled: true,
      hide_platform_templates: false,
    })
    expect(cap.value).toMatchObject({ method: 'PUT', path: '/api/admin/prompt-assistant/config' })
    expect(cap.value?.ifMatch).toBeNull()
  })

  it('patchTemplate PATCHes with If-Match: String(version) and the body', async () => {
    const cap = captureAll()
    await promptStudioApi.patchTemplate({ kind: 'user' }, 't_1', 2, { name: 'renamed' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/me/prompt-templates/t_1',
      ifMatch: '2',
      body: { name: 'renamed' },
    })
  })

  it('createTemplate POSTs the body to the org route', async () => {
    const cap = captureAll()
    await promptStudioApi.createTemplate({ kind: 'org', orgId: 'o_1' }, {
      name: 'N',
      description: 'D',
      body: 'B',
    })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/o_1/prompt-templates',
      body: { name: 'N', description: 'D', body: 'B' },
    })
  })

  it('deleteFile / deleteTemplate DELETE their scoped routes', async () => {
    const cap = captureAll()
    await promptStudioApi.deleteFile({ kind: 'org', orgId: 'o_1' }, 'f_1')
    expect(cap.value).toMatchObject({
      method: 'DELETE',
      path: '/api/orgs/o_1/prompt-assistant/config/files/f_1',
    })
    await promptStudioApi.deleteTemplate({ kind: 'platform' }, 't_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/admin/prompt-templates/t_1' })
  })

  // ---- multipart ----
  it('uploadFile POSTs to the scoped files route and returns the file', async () => {
    let hit: string | null = null
    server.use(
      mswHttp.post('/api/orgs/:oid/prompt-assistant/config/files', ({ request }) => {
        hit = new URL(request.url).pathname
        return HttpResponse.json(fileOut, { status: 201 })
      }),
    )
    const file = new File(['x'], 'sys.txt', { type: 'text/plain' })
    const out = await promptStudioApi.uploadFile({ kind: 'org', orgId: 'o_1' }, file)
    expect(hit).toBe('/api/orgs/o_1/prompt-assistant/config/files')
    expect(out).toMatchObject({ id: 'f_1', scan_status: 'pending' })
  })

  // ---- project-scoped ----
  it('resolvedForProject / mergedTemplates GET the project routes', async () => {
    const cap = captureAll()
    const r = await promptStudioApi.resolvedForProject('p_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/p_1/prompt-assistant' })
    expect(r).toMatchObject({ available: true })
    await promptStudioApi.mergedTemplates('p_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/p_1/prompt-templates' })
  })

  it('createSession POSTs and resolves { session_id }', async () => {
    const cap = captureAll()
    const created = await promptStudioApi.createSession('p_1')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/p_1/prompt-assistant/sessions',
    })
    expect(created).toEqual({ session_id: 'sess_1' })
  })

  it('postMessage POSTs { content, editor_draft } to the session route', async () => {
    const cap = captureAll()
    await promptStudioApi.postMessage('sess_1', { content: 'hi', editor_draft: null })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/prompt-assistant/sessions/sess_1/messages',
      body: { content: 'hi', editor_draft: null },
    })
  })

  it('getModelCatalog GETs /model-catalog', async () => {
    const cap = captureAll()
    const cat = await promptStudioApi.getModelCatalog()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/model-catalog' })
    expect(cat).toEqual({ chat: [] })
  })
})

import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'

import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { skillsApi } from '..'

// Request-level characterization of the skills api wire contract: each scoped method
// hits the right per-scope endpoint (agent -> /agents/{id}, project -> /projects/{id},
// org -> /orgs/{id}, platform -> /admin), patch/delete send If-Match for a version and
// omit it for null, multipart routes carry the file, and the scope-neutral binding,
// bundle-status, and metrics endpoints are unchanged.

const skill = { id: 's_1', name: 'pdf', scope: 'project', version: 3 }
const fileOut = { id: 'f_1', path: 'references/a.md', kind: 'reference', scan_status: 'pending' }
const job = { job_id: 'j_1', status: 'queued' }

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    // list (4 scopes)
    on('get', '/api/agents/:aid/skills', { items: [], total: 0 }),
    on('get', '/api/projects/:pid/skills', { items: [], total: 0 }),
    on('get', '/api/orgs/:oid/skills', { items: [], total: 0 }),
    on('get', '/api/admin/skills', { items: [], total: 0 }),
    // get / patch / delete / restore / copy (project + admin exemplars)
    on('get', '/api/projects/:pid/skills/:sid', skill),
    on('patch', '/api/projects/:pid/skills/:sid', skill),
    on('delete', '/api/projects/:pid/skills/:sid', null, 204),
    on('post', '/api/projects/:pid/skills/:sid/restore', skill),
    on('post', '/api/admin/skills/:sid/copy', skill),
    on('post', '/api/orgs/:oid/skills', skill, 201),
    // files
    on('get', '/api/projects/:pid/skills/:sid/files', [fileOut]),
    on('post', '/api/projects/:pid/skills/:sid/files', fileOut, 201),
    on('patch', '/api/projects/:pid/skills/:sid/files/:fid', fileOut),
    on('delete', '/api/projects/:pid/skills/:sid/files/:fid', null, 204),
    // bundle transport + status
    on('post', '/api/projects/:pid/skills/import', job, 202),
    on('get', '/api/projects/:pid/skills/:sid/export', job, 202),
    on('get', '/api/skills/imports/:tid', { job_id: 'j_1', status: 'ready', warnings: [] }),
    on('get', '/api/skills/exports/:tid', { job_id: 'j_1', status: 'ready', url: 'x' }),
    // bindings + metrics
    on('get', '/api/agents/:aid/skill-bindings', []),
    on('put', '/api/agents/:aid/skill-bindings/:sid', null, 204),
    on('delete', '/api/agents/:aid/skill-bindings/:sid', null, 204),
    on('get', '/api/admin/skills/metrics', { counts: {}, total: 0 }),
  )
  return cap
}

describe('skills api wire contract', () => {
  it('list dispatches agent -> /agents, project -> /projects, org -> /orgs, platform -> /admin', async () => {
    const cap = captureAll()
    await skillsApi.list({ kind: 'agent', agentId: 'a_1' })
    expect(cap.value?.path).toBe('/api/agents/a_1/skills')
    await skillsApi.list({ kind: 'project', projectId: 'p_1' })
    expect(cap.value?.path).toBe('/api/projects/p_1/skills')
    await skillsApi.list({ kind: 'org', orgId: 'o_1' })
    expect(cap.value?.path).toBe('/api/orgs/o_1/skills')
    await skillsApi.list({ kind: 'platform' })
    expect(cap.value?.path).toBe('/api/admin/skills')
  })

  it('get dispatches to the scoped detail route', async () => {
    const cap = captureAll()
    await skillsApi.get({ kind: 'project', projectId: 'p_1' }, 's_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/p_1/skills/s_1' })
  })

  it('create POSTs the body to the scoped route', async () => {
    const cap = captureAll()
    await skillsApi.create({ kind: 'org', orgId: 'o_1' }, {
      name: 'n',
      description: 'd',
      body: '',
      requires: [],
      allowed_tools: [],
    })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/orgs/o_1/skills',
      body: { name: 'n', description: 'd' },
    })
  })

  it('patch sends If-Match for a version and omits it for null', async () => {
    const cap = captureAll()
    await skillsApi.patch({ kind: 'project', projectId: 'p_1' }, 's_1', 4, { description: 'x' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/projects/p_1/skills/s_1',
      ifMatch: '4',
      body: { description: 'x' },
    })
    await skillsApi.patch({ kind: 'project', projectId: 'p_1' }, 's_1', null, { description: 'y' })
    expect(cap.value?.ifMatch).toBeNull()
  })

  it('remove sends If-Match; restore POSTs its route', async () => {
    const cap = captureAll()
    await skillsApi.remove({ kind: 'project', projectId: 'p_1' }, 's_1', 2)
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/projects/p_1/skills/s_1', ifMatch: '2' })
    await skillsApi.restore({ kind: 'project', projectId: 'p_1' }, 's_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/projects/p_1/skills/s_1/restore' })
  })

  it('createFile / deleteFile hit the scoped files routes', async () => {
    const cap = captureAll()
    await skillsApi.createFile({ kind: 'project', projectId: 'p_1' }, 's_1', {
      path: 'references/a.md',
      content: 'hi',
    })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/p_1/skills/s_1/files',
      body: { path: 'references/a.md', content: 'hi' },
    })
    await skillsApi.deleteFile({ kind: 'project', projectId: 'p_1' }, 's_1', 'f_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/projects/p_1/skills/s_1/files/f_1' })
  })

  it('uploadFile POSTs multipart to the scoped upload route', async () => {
    let hit: string | null = null
    server.use(
      mswHttp.post('/api/projects/:pid/skills/:sid/files/upload', ({ request }) => {
        hit = new URL(request.url).pathname
        return HttpResponse.json(fileOut, { status: 201 })
      }),
    )
    const file = new File(['x'], 'a.md', { type: 'text/markdown' })
    const out = await skillsApi.uploadFile({ kind: 'project', projectId: 'p_1' }, 's_1', 'references/a.md', file)
    expect(hit).toBe('/api/projects/p_1/skills/s_1/files/upload')
    expect(out).toMatchObject({ id: 'f_1' })
  })

  it('importBundle multipart POSTs, exportBundle GETs, status GETs are scope-neutral', async () => {
    const cap = captureAll()
    // Registered after captureAll so this (last-added) handler wins for the import route.
    let importHit: string | null = null
    server.use(
      mswHttp.post('/api/projects/:pid/skills/import', ({ request }) => {
        importHit = new URL(request.url).pathname
        return HttpResponse.json(job, { status: 202 })
      }),
    )
    const file = new File(['x'], 'b.zip', { type: 'application/zip' })
    await skillsApi.importBundle({ kind: 'project', projectId: 'p_1' }, file)
    expect(importHit).toBe('/api/projects/p_1/skills/import')
    await skillsApi.exportBundle({ kind: 'project', projectId: 'p_1' }, 's_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/p_1/skills/s_1/export' })
    await skillsApi.importStatus('t_1')
    expect(cap.value?.path).toBe('/api/skills/imports/t_1')
    await skillsApi.exportStatus('t_1')
    expect(cap.value?.path).toBe('/api/skills/exports/t_1')
  })

  it('bindings: list GET, bind PUT, unbind DELETE; metrics GET', async () => {
    const cap = captureAll()
    await skillsApi.listBindings('a_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/agents/a_1/skill-bindings' })
    await skillsApi.bind('a_1', 's_1')
    expect(cap.value).toMatchObject({ method: 'PUT', path: '/api/agents/a_1/skill-bindings/s_1' })
    await skillsApi.unbind('a_1', 's_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/agents/a_1/skill-bindings/s_1' })
    await skillsApi.metrics()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/admin/skills/metrics' })
  })
})

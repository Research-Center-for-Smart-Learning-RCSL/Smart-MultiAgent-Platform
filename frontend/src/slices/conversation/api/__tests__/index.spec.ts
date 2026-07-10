import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import * as api from '..'

// Request-level characterization of the conversation api wire contract, pinned as
// docs/tasks/2026-07-10-generated-client-wrap-conversation converts the slice from
// @shared/transport's `http` to the generated services. Assertions cover the
// OUTBOUND request (verb / path / query / If-Match / body shape) plus the four
// boundary bridges whose RETURN value is transformed (presence unwrap, the
// discriminated release_target, BoundAgentRef.role normalization, multipart
// upload). The slice already returned bare bodies before the conversion, so the
// return-shape assertions hold on both sides of the swap.

interface Captured {
  method: string
  path: string
  query: Record<string, string>
  ifMatch: string | null
  body: unknown
}

const workspaceOut = {
  id: 'ws_1',
  project_id: 'proj_1',
  name: 'Workspace',
  concept_map_enabled: false,
  created_at: 't',
  deleted_at: null,
}
const chatroomOut = {
  id: 'cr_1',
  workspace_id: 'ws_1',
  name: 'Room',
  allow_org_members: false,
  allow_project_members: true,
  allow_project_owners_only: false,
  allow_guest_links: false,
  version: 1,
  created_at: 't',
  created_by_user_id: null,
  deleted_at: null,
  disclose_observers: false,
  observers_present: false,
}
const messageOut = {
  id: 'm_1',
  chatroom_id: 'cr_1',
  sender_type: 'agent',
  sender_id: 'a_1',
  content_md: 'hi',
  metadata: {},
  version: 1,
  created_at: 't',
  edited_at: null,
  deleted_at: null,
}
const observationOut = {
  id: 'o_1',
  chatroom_id: 'cr_1',
  agent_id: 'a_1',
  content_md: 'note',
  metadata: {},
  trigger: 'silence_minutes',
  trigger_message_id: null,
  released_at: null,
  release_target: { kind: 'room' },
  released_by_user_id: null,
  created_at: 't',
}
const attachmentOut = {
  id: 'at_1',
  chatroom_id: 'cr_1',
  message_id: null,
  filename: 'f.txt',
  mime: 'text/plain',
  size_bytes: 10,
  status: 'active',
  scan_status: 'clean',
}

// Register one capturing handler per endpoint the slice touches, each returning a
// benign success so the wrapper resolves. `.value` holds the last captured request.
function captureAll(): { value: Captured | null } {
  const holder: { value: Captured | null } = { value: null }
  const record = async (request: Request): Promise<void> => {
    const url = new URL(request.url)
    let body: unknown = undefined
    if (request.method !== 'GET' && request.method !== 'DELETE') {
      // JSON bodies parse; the one multipart upload has an unparseable node
      // form-data stream in this env, so it records as undefined (that test
      // asserts verb/path/return, not the body).
      body = await request.clone().json().catch(() => undefined)
    }
    holder.value = {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      ifMatch: request.headers.get('if-match'),
      body,
    }
  }
  const ok = (json: unknown, status = 200): HttpResponse =>
    json === null ? new HttpResponse(null, { status }) : HttpResponse.json(json, { status })
  const on = (
    method: 'get' | 'post' | 'patch' | 'put' | 'delete',
    path: string,
    json: unknown,
    status = 200,
  ) =>
    mswHttp[method](path, async ({ request }) => {
      await record(request)
      return ok(json, status)
    })

  server.use(
    on('get', '/api/projects/:projectId/workspaces', [workspaceOut]),
    on('post', '/api/projects/:projectId/workspaces', { ...workspaceOut, default_chatroom_id: 'cr_1' }, 201),
    on('get', '/api/workspaces/:workspaceId', workspaceOut),
    on('delete', '/api/workspaces/:workspaceId', null, 204),
    on('put', '/api/workspaces/:workspaceId/concept-map-enabled', { workspace_id: 'ws_1', concept_map_enabled: true }),
    on('get', '/api/workspaces/:workspaceId/chatrooms', [chatroomOut]),
    on('post', '/api/workspaces/:workspaceId/chatrooms', chatroomOut, 201),
    on('patch', '/api/chatrooms/:chatroomId', chatroomOut),
    on('get', '/api/chatrooms/:chatroomId', chatroomOut),
    on('delete', '/api/chatrooms/:chatroomId', null, 204),
    on('get', '/api/chatrooms/:chatroomId/guest-link', { chatroom_id: 'cr_1', guest_token: 'tok', url: 'https://x/guest' }),
    on('get', '/api/projects/:projectId/agents', [{ id: 'a_1', name: 'Agent' }]),
    on('get', '/api/chatrooms/:chatroomId/agents', [
      { agent_id: 'a_1', role: null },
      { agent_id: 'a_2', role: 'observer' },
    ]),
    on('get', '/api/chatrooms/:chatroomId/members', [{ user_id: 'u_1', display_name: null }]),
    on('post', '/api/chatrooms/:chatroomId/agents', null, 204),
    on('patch', '/api/chatrooms/:chatroomId/agents/:agentId', null, 204),
    on('delete', '/api/chatrooms/:chatroomId/agents/:agentId', null, 204),
    on('get', '/api/chatrooms/:chatroomId/observations', [observationOut]),
    on('post', '/api/chatrooms/:chatroomId/observations/:observationId/release', observationOut),
    on('delete', '/api/chatrooms/:chatroomId/observations/:observationId', null, 204),
    on('get', '/api/chatrooms/:chatroomId/messages', [messageOut]),
    on('post', '/api/chatrooms/:chatroomId/messages', messageOut, 201),
    on('get', '/api/messages/:messageId', messageOut),
    on('get', '/api/chatrooms/:chatroomId/presence', { user_ids: ['u_1', 'u_2'] }),
    on('patch', '/api/messages/:messageId', messageOut),
    on('delete', '/api/messages/:messageId', null, 204),
    on('post', '/api/chatrooms/:chatroomId/attachments', attachmentOut, 201),
    on('get', '/api/attachments/:attachmentId', { ...attachmentOut, url: 'https://x/dl' }),
    on('get', '/api/chatrooms/:chatroomId/search', { query: 'hello', hits: [] }),
    on('post', '/api/chatrooms/:chatroomId/export', { job_id: 'j_1', status: 'queued' }),
    on('get', '/api/exports/:jobId', { job_id: 'j_1', chatroom_id: 'cr_1', status: 'ready', url: null, error: null }),
    on('post', '/api/guest/:chatroomId/:guestToken/enroll', null, 204),
    on('post', '/api/chatrooms/:chatroomId/compact', {}, 202),
  )
  return holder
}

describe('conversation api wire contract', () => {
  // ---- workspaces ----
  it('listWorkspaces GETs the project workspaces', async () => {
    const cap = captureAll()
    await api.listWorkspaces('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/workspaces' })
  })

  it('createWorkspace POSTs { name } and returns the workspace body', async () => {
    const cap = captureAll()
    const ws = await api.createWorkspace('proj_1', { name: 'New' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/workspaces',
      body: { name: 'New' },
    })
    expect(ws).toMatchObject({ id: 'ws_1', concept_map_enabled: false })
  })

  it('getWorkspace GETs a single workspace', async () => {
    const cap = captureAll()
    await api.getWorkspace('ws_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workspaces/ws_1' })
  })

  it('deleteWorkspace DELETEs the workspace', async () => {
    const cap = captureAll()
    await api.deleteWorkspace('ws_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/workspaces/ws_1' })
  })

  it('setWorkspaceConceptMapEnabled PUTs { enabled } and returns the status', async () => {
    const cap = captureAll()
    const res = await api.setWorkspaceConceptMapEnabled('ws_1', true)
    expect(cap.value).toMatchObject({
      method: 'PUT',
      path: '/api/workspaces/ws_1/concept-map-enabled',
      body: { enabled: true },
    })
    expect(res).toEqual({ workspace_id: 'ws_1', concept_map_enabled: true })
  })

  // ---- chatrooms ----
  it('listChatrooms GETs the workspace chatrooms', async () => {
    const cap = captureAll()
    await api.listChatrooms('ws_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workspaces/ws_1/chatrooms' })
  })

  it('createChatroom POSTs the payload to the workspace', async () => {
    const cap = captureAll()
    await api.createChatroom('ws_1', { name: 'Room', allow_org_members: true })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/workspaces/ws_1/chatrooms',
      body: { name: 'Room', allow_org_members: true },
    })
  })

  it('patchChatroom PATCHes with If-Match set to the version', async () => {
    const cap = captureAll()
    await api.patchChatroom('cr_1', 7, { name: 'Renamed' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/chatrooms/cr_1',
      ifMatch: '7',
      body: { name: 'Renamed' },
    })
  })

  it('getChatroom GETs a single chatroom', async () => {
    const cap = captureAll()
    await api.getChatroom('cr_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1' })
  })

  it('deleteChatroom DELETEs the chatroom', async () => {
    const cap = captureAll()
    await api.deleteChatroom('cr_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/chatrooms/cr_1' })
  })

  it('getGuestLink GETs the guest link and exposes url', async () => {
    const cap = captureAll()
    const link = await api.getGuestLink('cr_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/guest-link' })
    expect(link.url).toBe('https://x/guest')
  })

  // ---- agent bindings ----
  it('listProjectAgents GETs the project agents', async () => {
    const cap = captureAll()
    await api.listProjectAgents('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/agents' })
  })

  it('listChatroomAgents normalizes a null role to absent (bridge B2)', async () => {
    const cap = captureAll()
    const refs = await api.listChatroomAgents('cr_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/agents' })
    expect(refs).toEqual([{ agent_id: 'a_1' }, { agent_id: 'a_2', role: 'observer' }])
    expect('role' in refs[0]).toBe(false)
  })

  it('listChatroomMembers GETs the members', async () => {
    const cap = captureAll()
    await api.listChatroomMembers('cr_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/members' })
  })

  it('addChatroomAgent POSTs { agent_id, role? }', async () => {
    const cap = captureAll()
    await api.addChatroomAgent('cr_1', 'a_9', 'observer')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/chatrooms/cr_1/agents',
      body: { agent_id: 'a_9', role: 'observer' },
    })
  })

  it('setChatroomAgentRole PATCHes { role }', async () => {
    const cap = captureAll()
    await api.setChatroomAgentRole('cr_1', 'a_9', 'normal')
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/chatrooms/cr_1/agents/a_9',
      body: { role: 'normal' },
    })
  })

  it('removeChatroomAgent DELETEs a single binding', async () => {
    const cap = captureAll()
    await api.removeChatroomAgent('cr_1', 'a_9')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/chatrooms/cr_1/agents/a_9' })
  })

  // ---- observations ----
  it('listObservations maps release_target to the discriminated shape (bridge B1)', async () => {
    const cap = captureAll()
    const rows = await api.listObservations('cr_1', { limit: 20 })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/observations' })
    expect(cap.value?.query.limit).toBe('20')
    expect(rows[0].release_target).toEqual({ kind: 'room' })
  })

  it('releaseObservation POSTs the flat ReleaseIn body and returns an Observation (bridge B1)', async () => {
    const cap = captureAll()
    const obs = await api.releaseObservation('cr_1', 'o_1', {
      target: 'agents',
      agent_ids: ['a_1'],
      wake: true,
    })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/chatrooms/cr_1/observations/o_1/release',
      body: { target: 'agents', agent_ids: ['a_1'], wake: true },
    })
    expect(obs.release_target).toEqual({ kind: 'room' })
  })

  it('deleteObservation DELETEs a single observation', async () => {
    const cap = captureAll()
    await api.deleteObservation('cr_1', 'o_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/chatrooms/cr_1/observations/o_1' })
  })

  // ---- messages ----
  it('listMessages GETs with paging query', async () => {
    const cap = captureAll()
    await api.listMessages('cr_1', { before: 'm_5', limit: 30 })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/messages' })
    expect(cap.value?.query).toMatchObject({ before: 'm_5', limit: '30' })
  })

  it('sendMessage POSTs the content payload', async () => {
    const cap = captureAll()
    await api.sendMessage('cr_1', { content_md: 'yo', attachment_ids: ['at_1'] })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/chatrooms/cr_1/messages',
      body: { content_md: 'yo', attachment_ids: ['at_1'] },
    })
  })

  it('getMessage GETs a single message', async () => {
    const cap = captureAll()
    await api.getMessage('m_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/messages/m_1' })
  })

  it('getChatroomPresence unwraps user_ids to a string[]', async () => {
    const cap = captureAll()
    const ids = await api.getChatroomPresence('cr_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/presence' })
    expect(ids).toEqual(['u_1', 'u_2'])
  })

  it('editMessage PATCHes with If-Match and content', async () => {
    const cap = captureAll()
    await api.editMessage('m_1', 3, 'edited')
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/messages/m_1',
      ifMatch: '3',
      body: { content_md: 'edited' },
    })
  })

  it('deleteMessage DELETEs the message', async () => {
    const cap = captureAll()
    await api.deleteMessage('m_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/messages/m_1' })
  })

  // ---- attachments ----
  it('uploadSingleShot POSTs the file to the attachments route and returns it (bridge B3)', async () => {
    // The generated core builds the multipart body with the `form-data` package;
    // under vitest's node resolution that stream isn't introspectable via
    // request.formData(), so we assert the verb/path and the end-to-end return
    // (a resolved attachment proves the multipart call went through). The browser
    // field of `form-data` maps to native FormData, so production is unaffected.
    const cap = captureAll()
    const file = new File(['data'], 'note.txt', { type: 'text/plain' })
    const att = await api.uploadSingleShot('cr_1', file)
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/chatrooms/cr_1/attachments' })
    expect(att).toMatchObject({ id: 'at_1', status: 'active', scan_status: 'clean' })
  })

  it('getAttachment GETs a single attachment download', async () => {
    const cap = captureAll()
    const dl = await api.getAttachment('at_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/attachments/at_1' })
    expect(dl.url).toBe('https://x/dl')
  })

  // ---- search + export ----
  it('searchMessages GETs with q + limit query', async () => {
    const cap = captureAll()
    await api.searchMessages('cr_1', 'hello', 25)
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/chatrooms/cr_1/search' })
    expect(cap.value?.query).toMatchObject({ q: 'hello', limit: '25' })
  })

  it('createExport POSTs options and returns { job_id, status }', async () => {
    const cap = captureAll()
    const job = await api.createExport('cr_1', { format: 'json' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/chatrooms/cr_1/export',
      body: { format: 'json' },
    })
    expect(job).toEqual({ job_id: 'j_1', status: 'queued' })
  })

  it('getExport GETs the export job status', async () => {
    const cap = captureAll()
    const status = await api.getExport('j_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/exports/j_1' })
    expect(status).toMatchObject({ job_id: 'j_1', status: 'ready' })
  })

  // ---- guests + compact ----
  it('enrollGuest POSTs the display name to the guest enroll route', async () => {
    const cap = captureAll()
    await api.enrollGuest('cr_1', 'gtok', 'Guest Bob')
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/guest/cr_1/gtok/enroll',
      body: { display_name: 'Guest Bob' },
    })
  })

  it('compactChatroom POSTs to the compact route', async () => {
    const cap = captureAll()
    await api.compactChatroom('cr_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/chatrooms/cr_1/compact' })
  })
})

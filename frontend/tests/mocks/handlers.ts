import { http, HttpResponse } from 'msw'

export const handlers = [
  http.post('/api/auth/login', () =>
    HttpResponse.json({
      access_token: 'test-access',
      refresh_token: 'test-refresh',
      expires_in: 3600,
    }),
  ),

  http.get('/api/auth/captcha-config', () =>
    HttpResponse.json({ mode: 'off', provider: 'off', sitekey: '' }),
  ),

  http.post('/api/auth/logout', () => new HttpResponse(null, { status: 204 })),

  http.post('/api/auth/refresh', () =>
    HttpResponse.json({
      access_token: 'refreshed-access',
      refresh_token: 'refreshed-refresh',
      expires_in: 3600,
    }),
  ),

  http.get('/api/auth/me', () =>
    HttpResponse.json({
      id: 'u_test',
      email: 'test@example.com',
      display_name: 'Test User',
      email_verified: true,
      is_admin: false,
      created_at: '2026-01-01T00:00:00Z',
    }),
  ),

  http.get('/api/orgs', () => HttpResponse.json([])),
  http.get('/api/orgs/:orgId', () =>
    HttpResponse.json({ id: 'org_1', name: 'Test Org', owner_id: 'u_test' }),
  ),
  http.get('/api/orgs/:orgId/members', () => HttpResponse.json([])),
  http.get('/api/orgs/:orgId/projects', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId', () =>
    HttpResponse.json({ id: 'proj_1', name: 'Test Project', org_id: 'org_1' }),
  ),
  http.get('/api/projects/:projectId/members', () => HttpResponse.json([])),

  http.get('/api/keys', () => HttpResponse.json([])),
  http.get('/api/keys/:keyId', () =>
    HttpResponse.json({
      id: 'key_1',
      provider: 'openai',
      label: 'My Key',
      masked_key: 'sk-****',
    }),
  ),
  http.get('/api/key-groups', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/keys', () => HttpResponse.json([])),
  http.get('/api/search-keys', () => HttpResponse.json([])),

  http.get('/api/projects/:projectId/agents', () => HttpResponse.json([])),
  http.get('/api/agents/:agentId', () =>
    HttpResponse.json({
      id: 'agent_1',
      name: 'Test Agent',
      project_id: 'proj_1',
      system_prompt: '',
    }),
  ),

  http.get('/api/chatrooms', () => HttpResponse.json([])),
  http.get('/api/chatrooms/:chatroomId', () =>
    HttpResponse.json({
      id: 'cr_1', name: 'Test Room', project_id: 'proj_1',
      workspace_id: 'ws_1',
      allow_org_members: false, allow_project_members: true,
      allow_project_owners_only: false, allow_guest_links: false,
      agents: [],
    }),
  ),
  http.get('/api/chatrooms/:chatroomId/messages', () => HttpResponse.json([])),
  http.get('/api/workspaces', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/workspaces', () => HttpResponse.json([])),
  http.get('/api/workspaces/:workspaceId/chatrooms', () => HttpResponse.json([])),
  http.get('/api/workspaces/:workspaceId/workflows', () => HttpResponse.json([])),
  http.post('/api/guest/:chatroomId/:guestToken/enroll', () =>
    new HttpResponse(null, { status: 204 }),
  ),

  http.get('/api/projects/:projectId/workflows', () => HttpResponse.json([])),
  http.get('/api/workflows/:workflowId', () =>
    HttpResponse.json({
      id: 'wf_1', name: 'Test Workflow', project_id: 'proj_1',
      workspace_id: 'ws_1', version: 1,
      definition: { nodes: [], edges: [] },
    }),
  ),
  http.get('/api/workflows/:workflowId/runs', () => HttpResponse.json([])),
  http.get('/api/workflow-runs/:runId', () =>
    HttpResponse.json({
      id: 'run_1', workflow_id: 'wf_1', state: 'completed',
      trigger_type: 'manual', started_at: '2026-01-01T00:00:00Z',
      ended_at: '2026-01-01T00:01:00Z',
    }),
  ),
  http.get('/api/workflow-runs/:runId/steps', () => HttpResponse.json([])),
  http.get('/api/workflow-runs/:runId/approvals', () => HttpResponse.json([])),

  http.get('/api/admin/users', () => HttpResponse.json({ items: [], total: 0 })),
  http.get('/api/admin/users/:userId', () =>
    HttpResponse.json({
      id: 'u_1',
      email: 'admin@example.com',
      display_name: 'Admin',
      status: 'active',
      email_verified: true,
      is_admin: true,
      banned_reason: null,
      banned_at: null,
      deleted_at: null,
      last_login_at: null,
      created_at: '2026-01-01T00:00:00Z',
      org_ids: [],
      project_ids: [],
    }),
  ),
  http.get('/api/admin/orgs', () => HttpResponse.json({ items: [], total: 0 })),
  http.get('/api/admin/projects', () => HttpResponse.json({ items: [], total: 0 })),
  http.get('/api/admin/audit', () => HttpResponse.json({ items: [], total: 0 })),
  http.get('/api/admin/metrics', () => HttpResponse.json({})),
  http.get('/api/admin/rate-limits', () => HttpResponse.json([])),
  http.get('/api/admin/ip-bans', () => HttpResponse.json([])),
  http.get('/api/admin/admins', () => HttpResponse.json([])),
  http.get('/api/admin/ops', () => HttpResponse.json({})),

  http.get('/api/orgs/:orgId/original-creator-transfers', () => HttpResponse.json([])),

  http.get('/api/invites/inbox', () => HttpResponse.json([])),
  http.get('/api/invites', () => HttpResponse.json([])),
  http.post('/api/invites/accept-by-token', () =>
    HttpResponse.json({
      id: 'inv_1',
      scope_type: 'org',
      scope_id: 'org_1',
      scope_name: 'Test Org',
      invitee_email: 'test@example.com',
      role: 'member',
      state: 'accepted',
      created_at: '2026-01-01T00:00:00Z',
      expires_at: '2026-01-08T00:00:00Z',
    }),
  ),
  http.get('/api/projects', () => HttpResponse.json([])),

  http.get('/api/guest/:token', () =>
    HttpResponse.json({ chatroom_id: 'cr_1', display_name: 'Guest' }),
  ),
]

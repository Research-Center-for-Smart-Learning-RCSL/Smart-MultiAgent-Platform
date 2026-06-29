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
      status: 'active',
      created_at: '2026-01-01T00:00:00Z',
    }),
  ),

  http.patch('/api/auth/me', async ({ request }) => {
    const body = (await request.json()) as { display_name: string | null }
    return HttpResponse.json({
      id: 'u_test',
      email: 'test@example.com',
      display_name: body.display_name?.trim() || null,
      email_verified: true,
      is_admin: false,
      status: 'active',
    })
  }),

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
  http.get('/api/keys/:keyId/projects', () => HttpResponse.json([])),
  http.get('/api/keys/:keyId', ({ params }) =>
    HttpResponse.json({
      id: params.keyId,
      provider: 'openai',
      name: 'My Key',
      masked_preview: 'sk-****',
      test_status: 'ok',
      test_error: null,
      last_test_at: null,
      created_at: '2026-01-01T00:00:00Z',
    }),
  ),
  http.get('/api/key-groups', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/key-groups', () =>
    HttpResponse.json([
      { id: 'kg_1', project_id: 'proj_1', name: 'Default Group', created_at: '2026-01-01T00:00:00Z' },
    ]),
  ),
  http.get('/api/projects/:projectId/keys', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/rag-configs', () => HttpResponse.json([])),
  http.get('/api/search-keys', () => HttpResponse.json([])),

  http.get('/api/projects/:projectId/agents', () => HttpResponse.json([])),
  http.get('/api/agents/:agentId', () =>
    HttpResponse.json({
      id: 'agent_1',
      name: 'Test Agent',
      project_id: 'proj_1',
      model_hint: 'claude',
      model_id: null,
      effort: null,
      key_group_id: 'kg_1',
      system_prompt: '',
      prompt_strategy: 'full',
      rag_config_id: null,
      graphrag_config_id: null,
      context_mode: 'general',
      context_token_cap: null,
      a2a_enabled: false,
      wakeup_config: {},
      workflow_capabilities: {},
      version: 1,
      created_at: '2026-01-01T00:00:00Z',
      deleted_at: null,
    }),
  ),

  http.get('/api/model-catalog', () =>
    HttpResponse.json({
      chat: [
        { provider: 'claude', models: ['claude-opus-4-8', 'claude-sonnet-4-6'], default: 'claude-sonnet-4-6' },
        { provider: 'openai', models: ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini'], default: 'gpt-5.4' },
        { provider: 'gemini', models: ['gemini-3.5-flash', 'gemini-2.5-flash'], default: 'gemini-3.5-flash' },
      ],
      embedding: [
        {
          provider: 'openai',
          models: [
            { model: 'text-embedding-3-small', dimension: 1536 },
            { model: 'text-embedding-3-large', dimension: 3072 },
          ],
          default: 'text-embedding-3-small',
        },
        { provider: 'gemini', models: [{ model: 'text-embedding-004', dimension: 768 }], default: 'text-embedding-004' },
        { provider: 'voyage', models: [{ model: 'voyage-3', dimension: 1024 }], default: 'voyage-3' },
      ],
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
  http.get('/api/chatrooms/:chatroomId/agents', () => HttpResponse.json([])),
  http.get('/api/workspaces', () => HttpResponse.json([])),
  http.get('/api/workspaces/:workspaceId', () =>
    HttpResponse.json({
      id: 'ws_1',
      project_id: 'proj_1',
      name: 'Test Workspace',
      created_at: '2026-01-01T00:00:00Z',
      deleted_at: null,
    }),
  ),
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

  http.get('/api/admin/users', () => HttpResponse.json([])),
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
  http.get('/api/admin/orgs', () => HttpResponse.json([])),
  http.get('/api/admin/projects', () => HttpResponse.json([])),
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

  http.get('/api/chatrooms/:chatroomId/members', () => HttpResponse.json([])),
]

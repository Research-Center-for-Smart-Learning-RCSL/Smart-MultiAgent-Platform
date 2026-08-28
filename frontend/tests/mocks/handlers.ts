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

  http.post('/api/auth/ws-ticket', () =>
    HttpResponse.json({ ticket: 'test-ws-ticket', expires_in: 30 }),
  ),

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

  http.get('/api/auth/identities', () => HttpResponse.json([])),

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
  http.get('/api/projects/:projectId/invitable-members', () => HttpResponse.json([])),

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
  http.get('/api/projects/:projectId/graphrag-configs', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/graphrag-configs/owner-options', () => HttpResponse.json([])),
  http.get('/api/projects/:projectId/knowmap-configs', () => HttpResponse.json([])),
  http.get('/api/knowmap-configs/:configId/documents', () => HttpResponse.json([])),
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
      // Per-model capability rows (R9.03a) mirror the real backend table
      // (contexts/agents/domain/model_specs.py) for the ids that overlap.
      chat: [
        {
          provider: 'claude',
          default: 'claude-sonnet-4-6',
          models: [
            {
              model_id: 'claude-opus-4-8',
              context_limit: 1_000_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: false,
              accepts_vision: true,
              uses_completion_token_field: false,
              effort_conflicts_with_tools: false,
              source_url: 'https://docs.anthropic.com/en/docs/about-claude/models/overview',
              verified_on: '2026-06-01',
            },
            {
              model_id: 'claude-sonnet-4-6',
              context_limit: 1_000_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: true,
              accepts_vision: true,
              uses_completion_token_field: false,
              effort_conflicts_with_tools: false,
              source_url: 'https://docs.anthropic.com/en/docs/about-claude/models/overview',
              verified_on: '2026-06-01',
            },
            {
              model_id: 'claude-haiku-4-5',
              context_limit: 200_000,
              accepts_effort: false,
              effort_values: [],
              accepts_sampling: true,
              accepts_vision: true,
              uses_completion_token_field: false,
              effort_conflicts_with_tools: false,
              source_url: 'https://docs.anthropic.com/en/docs/about-claude/models/overview',
              verified_on: '2026-06-01',
            },
          ],
        },
        {
          provider: 'openai',
          default: 'gpt-5.4',
          models: [
            {
              model_id: 'gpt-5.5',
              context_limit: 128_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: false,
              accepts_vision: true,
              uses_completion_token_field: true,
              effort_conflicts_with_tools: true,
              source_url: 'https://platform.openai.com/docs/models',
              verified_on: '2026-06-01',
            },
            {
              model_id: 'gpt-5.4',
              context_limit: 128_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: false,
              accepts_vision: true,
              uses_completion_token_field: true,
              effort_conflicts_with_tools: true,
              source_url: 'https://platform.openai.com/docs/models',
              verified_on: '2026-06-01',
            },
            {
              model_id: 'gpt-5.4-mini',
              context_limit: 128_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: false,
              accepts_vision: true,
              uses_completion_token_field: true,
              effort_conflicts_with_tools: true,
              source_url: 'https://platform.openai.com/docs/models',
              verified_on: '2026-06-01',
            },
          ],
        },
        {
          provider: 'gemini',
          default: 'gemini-3.5-flash',
          models: [
            {
              model_id: 'gemini-3.5-flash',
              context_limit: 1_000_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: true,
              accepts_vision: true,
              uses_completion_token_field: false,
              effort_conflicts_with_tools: false,
              source_url: 'https://ai.google.dev/gemini-api/docs/models',
              verified_on: '2026-06-01',
            },
            {
              model_id: 'gemini-2.5-flash',
              context_limit: 1_000_000,
              accepts_effort: true,
              effort_values: ['low', 'medium', 'high'],
              accepts_sampling: true,
              accepts_vision: true,
              uses_completion_token_field: false,
              effort_conflicts_with_tools: false,
              source_url: 'https://ai.google.dev/gemini-api/docs/models',
              verified_on: '2026-06-01',
            },
          ],
        },
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
  http.get('/api/chatrooms/:chatroomId/activity-activations/active', () =>
    HttpResponse.json(null),
  ),

  http.get('/api/exports/:jobId', ({ params }) =>
    HttpResponse.json({
      job_id: params.jobId,
      chatroom_id: 'cr_1',
      status: 'ready',
      url: null,
      error: null,
    }),
  ),

  // MUST stay last — msw matches in order, so this only catches what nothing
  // above (or a test's own server.use) claimed. Without it an unmocked call is
  // passed through to a real socket and rejects long after the test's jsdom
  // window is torn down; a late .catch() touching window (i18n's `t`, a toast)
  // then surfaces as an unhandled error and fails the whole run. Answering here
  // keeps the rejection inside the test that caused it.
  http.all('/api/*', ({ request }) => {
    console.warn(`[msw] unmocked ${request.method} ${new URL(request.url).pathname} -> 404`)
    return HttpResponse.json({ detail: 'unmocked in tests' }, { status: 404 })
  }),
]

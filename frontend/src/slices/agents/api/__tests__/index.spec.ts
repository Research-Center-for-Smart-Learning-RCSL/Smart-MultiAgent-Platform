import { describe, expect, it } from 'vitest'
import { server } from '../../../../../tests/mocks/server'
import { createRequestCapture, type CapturedRequest } from '../../../../../tests/helpers/requestCapture'
import { agentsApi } from '..'

// Request-level characterization of the agents api wire contract, pinned as
// docs/tasks/2026-07-11-generated-client-wrap-agents converts the ~47 methods from
// @shared/transport's `http` to the generated services. Covers a representative
// read/write per capability group, the response bridges (toAgentTool,
// toToolTestResult), the multipart uploads, and the security-relevant
// request bodies (tool auth/clear_auth, egress hostname). Bodies matter here: this
// surface carries tool credentials and the egress allowlist.


const agentOut = {
  id: 'ag_1',
  project_id: 'proj_1',
  name: 'Agent',
  model_hint: 'claude',
  model_id: null,
  effort: null,
  key_group_id: 'kg_1',
  system_prompt: '',
  prompt_strategy: 'full',
  rag_config_id: null,
  knowmap_config_id: null,
  context_mode: 'general',
  context_token_cap: null,
  a2a_enabled: false,
  wakeup_config: {},
  workflow_capabilities: {},
  version: 1,
  created_at: 't',
  deleted_at: null,
}
// No config_warnings → exercises the toAgentTool default.
const agentToolOut = {
  id: 'tool_1',
  agent_id: 'ag_1',
  tool_type: 'hosted_mcp',
  enabled: true,
  display_name: null,
  config: {},
  created_at: 't',
}
// No error → exercises the toToolTestResult default.
const toolTestOut = { ok: true, tool_names: ['search'], duration_ms: 5, status: null }
const ragDocumentOut = {
  id: 'doc_1',
  rag_config_id: 'rc_1',
  filename: 'f.pdf',
  mime: 'application/pdf',
  size_bytes: 10,
  status: 'ready',
  scan_status: 'clean',
  sha256: 'abc',
  uploaded_at: 't',
  agent_ids: [],
}
const ragConfigOut = {
  id: 'rc_1',
  project_id: 'proj_1',
  name: 'RAG',
  chunk_strategy: 'fixed',
  chunk_params: {},
  embed_key_id: null,
  embed_provider: 'openai',
  embed_model: 'text-embedding-3-small',
  rerank_enabled: false,
  rerank_key_id: null,
  rerank_provider: null,
  rerank_model: null,
  top_k: 8,
  created_at: 't',
}
const graphragConfigOut = {
  id: 'gc_1',
  project_id: 'proj_1',
  owner_kind: 'agent_group',
  owner_id: 'grp_1',
  owner_name: 'Group',
  agent_id: null,
  builder_key_group_id: 'kg_2',
  trigger_config: {},
  recency_half_life_days: null,
  last_build_state: 'idle',
  last_build_at: null,
  last_build_error: null,
  created_at: 't',
  deleted_at: null,
}
const knowmapConfigOut = {
  id: 'km_1',
  project_id: 'proj_1',
  name: 'KM',
  builder_key_group_id: 'kg_2',
  chunk_strategy: 'fixed',
  chunk_params: {},
  embed_provider: null,
  embed_model: null,
  embed_dim: null,
  last_build_state: 'idle',
  last_build_at: null,
  last_build_error: null,
  created_at: 't',
  deleted_at: null,
}
const graphOut = { config_id: 'gc_1', nodes: [], edges: [], truncated: false }

function captureAll(): { value: CapturedRequest | null } {
  const { cap, on } = createRequestCapture()
  server.use(
    // agents
    on('get', '/api/projects/:pid/agents', [agentOut]),
    on('post', '/api/projects/:pid/agents', agentOut, 201),
    on('get', '/api/agents/:aid', agentOut),
    on('patch', '/api/agents/:aid', agentOut),
    on('delete', '/api/agents/:aid', null, 204),
    // tools
    on('get', '/api/agents/:aid/tools', [agentToolOut]),
    on('post', '/api/agents/:aid/tools', agentToolOut, 201),
    on('patch', '/api/agents/:aid/tools/:tid', agentToolOut),
    on('delete', '/api/agents/:aid/tools/:tid', null, 204),
    on('post', '/api/agents/:aid/tools/:tid/test', toolTestOut),
    // rag
    on('get', '/api/projects/:pid/rag-configs', [ragConfigOut]),
    on('post', '/api/projects/:pid/rag-configs', ragConfigOut, 201),
    on('get', '/api/rag-configs/:cid', ragConfigOut),
    on('patch', '/api/rag-configs/:cid', ragConfigOut),
    on('delete', '/api/rag-configs/:cid', null, 204),
    on('get', '/api/rag-configs/:cid/documents', [ragDocumentOut]),
    on('post', '/api/rag-configs/:cid/documents', ragDocumentOut, 201),
    on('delete', '/api/rag-documents/:did', null, 204),
    on('patch', '/api/rag-documents/:did/agents', ragDocumentOut),
    // model catalog
    on('get', '/api/model-catalog', { chat: [], embedding: [] }),
    // graphrag
    on('get', '/api/projects/:pid/graphrag-configs', [graphragConfigOut]),
    on('post', '/api/projects/:pid/graphrag-configs', graphragConfigOut, 201),
    on('get', '/api/graphrag/:cid', graphragConfigOut),
    on('patch', '/api/graphrag/:cid', graphragConfigOut),
    on('post', '/api/graphrag/:cid/build', { accepted: true, build_id: 'b_1', state: 'running' }, 202),
    on('get', '/api/graphrag/:cid/status', { id: 'gc_1', state: 'idle', last_build_at: null, last_build_error: null }),
    on('get', '/api/graphrag/:cid/graph', graphOut),
    // knowmap
    on('get', '/api/projects/:pid/knowmap-configs', [knowmapConfigOut]),
    on('post', '/api/projects/:pid/knowmap-configs', knowmapConfigOut, 201),
    on('post', '/api/knowmap-configs/:cid/rebuild', { status: 'enqueued', config_id: 'km_1' }, 202),
    on('get', '/api/knowmap-configs/:cid/graph', { ...graphOut, config_id: 'km_1' }),
    on('post', '/api/knowmap-configs/:cid/documents', { ...ragDocumentOut, knowmap_config_id: 'km_1' }, 201),
    // egress
    on('get', '/api/projects/:pid/mcp/egress-allowlist', []),
    on('post', '/api/projects/:pid/mcp/egress-allowlist', { id: 'e_1', project_id: 'proj_1', hostname: 'api.example.com', added_by_user_id: null, added_at: 't', note: null }, 201),
    on('delete', '/api/projects/:pid/mcp/egress-allowlist/:hostname', null, 204),
    // workspace files
    on('get', '/api/agents/:aid/workspace-files', []),
    on('post', '/api/agents/:aid/workspace-files', { id: 'wf_1', agent_id: 'ag_1', path: 'a.py', size_bytes: 3, mime: 'text/x-python', created_at: 't' }, 201),
    on('delete', '/api/agents/:aid/workspace-files/:fid', null, 204),
  )
  return cap
}

describe('agents api wire contract', () => {
  // ---- agents CRUD ----
  it('list GETs the project agents', async () => {
    const cap = captureAll()
    const agents = await agentsApi.list('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/agents' })
    expect(agents[0]).toMatchObject({ id: 'ag_1', model_hint: 'claude' })
  })

  it('create POSTs the payload', async () => {
    const cap = captureAll()
    await agentsApi.create('proj_1', { name: 'X' } as never)
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/projects/proj_1/agents', body: { name: 'X' } })
  })

  it('patch PATCHes with If-Match', async () => {
    const cap = captureAll()
    await agentsApi.patch('ag_1', 4, { name: 'Y' })
    expect(cap.value).toMatchObject({ method: 'PATCH', path: '/api/agents/ag_1', ifMatch: '4' })
  })

  it('remove DELETEs with If-Match', async () => {
    const cap = captureAll()
    await agentsApi.remove('ag_1', 4)
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/agents/ag_1', ifMatch: '4' })
  })

  // ---- tools (bridges + credentials) ----
  it('listTools defaults config_warnings to [] (bridge toAgentTool)', async () => {
    const cap = captureAll()
    const tools = await agentsApi.listTools('ag_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/agents/ag_1/tools' })
    expect(tools[0].config_warnings).toEqual([])
  })

  it('addTool POSTs the payload carrying auth (credential preserved)', async () => {
    const cap = captureAll()
    await agentsApi.addTool('ag_1', {
      tool_type: 'hosted_mcp',
      config: { source: 'url', reference: 'https://mcp.example.com', allowed_tools: ['search'] },
      auth: { token: 'secret-token' },
    } as never)
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/agents/ag_1/tools',
      body: { tool_type: 'hosted_mcp', auth: { token: 'secret-token' } },
    })
  })

  it('patchTool carries auth + clear_auth unchanged', async () => {
    const cap = captureAll()
    await agentsApi.patchTool('ag_1', 'tool_1', { auth: { token: 't2' }, clear_auth: false })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/agents/ag_1/tools/tool_1',
      body: { auth: { token: 't2' }, clear_auth: false },
    })
  })

  it('testTool defaults error to null (bridge toToolTestResult)', async () => {
    const cap = captureAll()
    const res = await agentsApi.testTool('ag_1', 'tool_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/agents/ag_1/tools/tool_1/test' })
    expect(res.error).toBeNull()
    expect(res.ok).toBe(true)
  })

  // ---- rag (incl. multipart) ----
  it('listRagConfigs GETs the project rag-configs', async () => {
    const cap = captureAll()
    await agentsApi.listRagConfigs('proj_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/projects/proj_1/rag-configs' })
  })

  it('patchRagConfig PATCHes the config', async () => {
    const cap = captureAll()
    await agentsApi.patchRagConfig('rc_1', { top_k: 5 })
    expect(cap.value).toMatchObject({ method: 'PATCH', path: '/api/rag-configs/rc_1', body: { top_k: 5 } })
  })

  it('listDocuments resolves the rag documents with their status/scan_status', async () => {
    const cap = captureAll()
    const docs = await agentsApi.listDocuments('rc_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/rag-configs/rc_1/documents' })
    expect(docs[0]).toMatchObject({ status: 'ready', scan_status: 'clean' })
  })

  it('uploadDocumentMultipart POSTs to the documents route and returns a doc', async () => {
    const cap = captureAll()
    const file = new File(['x'], 'f.pdf', { type: 'application/pdf' })
    const doc = await agentsApi.uploadDocumentMultipart('rc_1', file, ['ag_1'])
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/rag-configs/rc_1/documents' })
    expect(doc).toMatchObject({ id: 'doc_1', status: 'ready' })
  })

  it('setDocumentAgents PATCHes the allowlist', async () => {
    const cap = captureAll()
    await agentsApi.setDocumentAgents('doc_1', ['ag_1', 'ag_2'])
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/rag-documents/doc_1/agents',
      body: { agent_ids: ['ag_1', 'ag_2'] },
    })
  })

  // ---- model catalog ----
  it('getModelCatalog GETs the static catalog', async () => {
    const cap = captureAll()
    await agentsApi.getModelCatalog()
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/model-catalog' })
  })

  // ---- graphrag ----
  it('createGraphragConfig POSTs the owner payload', async () => {
    const cap = captureAll()
    await agentsApi.createGraphragConfig('proj_1', {
      owner_kind: 'agent_group',
      owner_id: 'grp_1',
      builder_key_group_id: 'kg_2',
      trigger_config: {},
      recency_half_life_days: null,
    })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/graphrag-configs',
      body: { owner_kind: 'agent_group', owner_id: 'grp_1' },
    })
  })

  it('buildGraphrag POSTs to the build route', async () => {
    const cap = captureAll()
    const res = await agentsApi.buildGraphrag('gc_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/graphrag/gc_1/build' })
    expect(res.accepted).toBe(true)
  })

  it('getGraphragGraph passes the limit query param', async () => {
    const cap = captureAll()
    await agentsApi.getGraphragGraph('gc_1', 250)
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/graphrag/gc_1/graph' })
    expect(cap.value?.query).toMatchObject({ limit: '250' })
  })

  it('getGraphragStatus GETs the status', async () => {
    const cap = captureAll()
    const status = await agentsApi.getGraphragStatus('gc_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/graphrag/gc_1/status' })
    expect(status.state).toBe('idle')
  })

  // ---- knowmap ----
  it('rebuildKnowmap POSTs to the rebuild route', async () => {
    const cap = captureAll()
    const ack = await agentsApi.rebuildKnowmap('km_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/knowmap-configs/km_1/rebuild' })
    expect(ack).toMatchObject({ status: 'enqueued', config_id: 'km_1' })
  })

  it('getKnowmapGraph passes the limit query param', async () => {
    const cap = captureAll()
    await agentsApi.getKnowmapGraph('km_1', 100)
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/knowmap-configs/km_1/graph' })
    expect(cap.value?.query).toMatchObject({ limit: '100' })
  })

  it('uploadKnowmapDocumentMultipart POSTs to the documents route', async () => {
    const cap = captureAll()
    const file = new File(['x'], 'f.pdf', { type: 'application/pdf' })
    await agentsApi.uploadKnowmapDocumentMultipart('km_1', file, ['ag_1'])
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/knowmap-configs/km_1/documents' })
  })

  // ---- egress allowlist (security surface) ----
  it('addEgressAllowlistEntry POSTs { hostname, note }', async () => {
    const cap = captureAll()
    await agentsApi.addEgressAllowlistEntry('proj_1', { hostname: 'api.example.com', note: 'partner' })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/projects/proj_1/mcp/egress-allowlist',
      body: { hostname: 'api.example.com', note: 'partner' },
    })
  })

  it('removeEgressAllowlistEntry path-encodes the hostname', async () => {
    const cap = captureAll()
    await agentsApi.removeEgressAllowlistEntry('proj_1', 'api.example.com')
    expect(cap.value).toMatchObject({
      method: 'DELETE',
      path: '/api/projects/proj_1/mcp/egress-allowlist/api.example.com',
    })
  })

  // ---- workspace files ----
  it('uploadWorkspaceFile POSTs to the workspace-files route', async () => {
    const cap = captureAll()
    const file = new File(['x'], 'a.py', { type: 'text/x-python' })
    const wf = await agentsApi.uploadWorkspaceFile('ag_1', file)
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/agents/ag_1/workspace-files' })
    expect(wf).toMatchObject({ id: 'wf_1', path: 'a.py' })
  })

  it('deleteWorkspaceFile DELETEs a single file', async () => {
    const cap = captureAll()
    await agentsApi.deleteWorkspaceFile('ag_1', 'wf_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/agents/ag_1/workspace-files/wf_1' })
  })
})

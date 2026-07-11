import { describe, expect, it } from 'vitest'
import { http as mswHttp, HttpResponse } from 'msw'
import { server } from '../../../../../tests/mocks/server'
import * as api from '..'

// Request-level characterization of the workflow api wire contract, pinned as
// docs/tasks/2026-07-11-generated-client-wrap-workflow converts the 20 functions from
// @shared/transport's `http` to the generated services. The module already returned
// bare bodies (conversation pattern), so this also guards the signature-preserving
// promise: verb/path/params/body must not move, the two If-Match preconditions must
// survive, and the two wakeup-config reshapes must behave identically.

interface Captured {
  method: string
  path: string
  query: Record<string, string>
  ifMatch: string | null
  body: unknown
}

const workflowOut = {
  id: 'wf_1',
  workspace_id: 'ws_1',
  name: 'Flow',
  definition: { entry_node_id: 'n0', nodes: [], edges: [] },
  version: 1,
  created_at: 't',
  deleted_at: null,
}
const runOut = {
  id: 'run_1',
  workflow_id: 'wf_1',
  trigger_type: 'manual',
  started_by_user_id: null,
  state: 'running',
  variables: {},
  started_at: 't',
  ended_at: null,
}
const stepOut = {
  id: 'step_1',
  run_id: 'run_1',
  node_id: 'n1',
  state: 'succeeded',
  started_at: 't',
  ended_at: 't',
  input: {},
  output: {},
  error: null,
}
const validateOut = { valid: true, errors: [], warnings: [] }
const approvalWithVotes = {
  id: 'ap_1',
  run_id: 'run_1',
  state: 'pending',
  mode: 'any',
  votes: [],
}
const instruction = {
  id: 'ins_1',
  chain_id: 'ch_1',
  path: [],
  depth: 0,
  issuer_agent_id: 'ag_1',
  target_agent_id: 'ag_2',
  payload: {},
  state: 'issued',
  issued_at: 't',
  resolved_at: null,
}
const subagent = {
  id: 'inst_1',
  agent_id: 'ag_1',
  parent_id: null,
  chatroom_id: null,
  run_context: {},
  task_description: null,
  state: 'alive',
  spawned_at: 't',
  destroyed_at: null,
}
const dlqEntry = {
  stream_entry_id: 'e_1',
  stream_id: 's_1',
  envelope: '{}',
  attempt_count: 3,
  last_error: 'boom',
  moved_at: 't',
}

function captureAll(): { value: Captured | null } {
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
      ifMatch: request.headers.get('if-match'),
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
    // workflow CRUD
    on('get', '/api/workspaces/:wid/workflows', [workflowOut]),
    on('post', '/api/workspaces/:wid/workflows', workflowOut, 201),
    on('patch', '/api/workflows/:wfid', workflowOut),
    on('delete', '/api/workflows/:wfid', null, 204),
    on('post', '/api/workspaces/:wid/workflows/validate', validateOut),
    // runs
    on('post', '/api/workflows/:wfid/runs', { run_id: 'run_1' }, 202),
    on('post', '/api/workflows/:wfid/dry-run', { run_id: 'dry_1' }, 202),
    on('get', '/api/workflows/:wfid/runs', [runOut]),
    on('get', '/api/workflow-runs/:rid', runOut),
    on('post', '/api/workflow-runs/:rid/cancel', { status: 'cancelling' }),
    on('get', '/api/workflow-runs/:rid/steps', [stepOut]),
    // orchestration
    on('get', '/api/orchestration/approvals/:aid', approvalWithVotes),
    on('get', '/api/orchestration/workflow-runs/:rid/approvals', [approvalWithVotes]),
    on('get', '/api/orchestration/instructions/:iid', instruction),
    on('get', '/api/orchestration/chains/:cid/instructions', [instruction]),
    on('get', '/api/orchestration/workflow-runs/:rid/subagents', [subagent]),
    on('get', '/api/orchestration/agents/:agid/dlq', [dlqEntry]),
    // agent wakeup config
    on('get', '/api/agents/:agid', { id: 'ag_1', version: 2, wakeup_config: { enabled: true } }),
    on('patch', '/api/agents/:agid', { id: 'ag_1', version: 3 }),
  )
  return holder
}

describe('workflow api wire contract', () => {
  // ---- workflow CRUD ----
  it('listWorkflows GETs the workspace workflows', async () => {
    const cap = captureAll()
    const rows = await api.listWorkflows('ws_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workspaces/ws_1/workflows' })
    expect(rows[0]).toMatchObject({ id: 'wf_1', definition: { entry_node_id: 'n0' } })
  })

  it('createWorkflow POSTs { name, definition }', async () => {
    const cap = captureAll()
    const def = { entry_node_id: 'n0', nodes: [], edges: [] }
    await api.createWorkflow('ws_1', { name: 'Flow', definition: def })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/workspaces/ws_1/workflows',
      body: { name: 'Flow', definition: def },
    })
  })

  it('patchWorkflow PATCHes with If-Match: String(version)', async () => {
    const cap = captureAll()
    await api.patchWorkflow('wf_1', 7, { name: 'Renamed' })
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/workflows/wf_1',
      ifMatch: '7',
      body: { name: 'Renamed' },
    })
  })

  it('deleteWorkflow DELETEs the workflow', async () => {
    const cap = captureAll()
    await api.deleteWorkflow('wf_1')
    expect(cap.value).toMatchObject({ method: 'DELETE', path: '/api/workflows/wf_1' })
  })

  it('validateWorkflow POSTs { definition } and resolves the ValidationResult', async () => {
    const cap = captureAll()
    const def = { entry_node_id: 'n0', nodes: [], edges: [] }
    const res = await api.validateWorkflow('ws_1', def)
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/workspaces/ws_1/workflows/validate',
      body: { definition: def },
    })
    expect(res).toMatchObject({ valid: true, errors: [], warnings: [] })
  })

  // ---- runs ----
  it('triggerRun POSTs { trigger_payload } and casts to { run_id }', async () => {
    const cap = captureAll()
    const res = await api.triggerRun('wf_1', { seed: 1 })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/workflows/wf_1/runs',
      body: { trigger_payload: { seed: 1 } },
    })
    expect(res).toEqual({ run_id: 'run_1' })
  })

  it('triggerRun defaults the trigger payload to {}', async () => {
    const cap = captureAll()
    await api.triggerRun('wf_1')
    expect(cap.value?.body).toEqual({ trigger_payload: {} })
  })

  it('dryRun POSTs to the dry-run route', async () => {
    const cap = captureAll()
    const res = await api.dryRun('wf_1', { seed: 2 })
    expect(cap.value).toMatchObject({
      method: 'POST',
      path: '/api/workflows/wf_1/dry-run',
      body: { trigger_payload: { seed: 2 } },
    })
    expect(res).toEqual({ run_id: 'dry_1' })
  })

  it('listRuns passes limit/offset/include_archive query params', async () => {
    const cap = captureAll()
    await api.listRuns('wf_1', { limit: 10, offset: 20, includeArchive: true })
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workflows/wf_1/runs' })
    expect(cap.value?.query).toMatchObject({ limit: '10', offset: '20', include_archive: 'true' })
  })

  it('listRuns applies the 50/0/false defaults', async () => {
    const cap = captureAll()
    await api.listRuns('wf_1')
    expect(cap.value?.query).toMatchObject({ limit: '50', offset: '0', include_archive: 'false' })
  })

  it('getRun GETs the run', async () => {
    const cap = captureAll()
    const run = await api.getRun('run_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workflow-runs/run_1' })
    expect(run).toMatchObject({ id: 'run_1', state: 'running' })
  })

  it('cancelRun POSTs to cancel and casts to { status }', async () => {
    const cap = captureAll()
    const res = await api.cancelRun('run_1')
    expect(cap.value).toMatchObject({ method: 'POST', path: '/api/workflow-runs/run_1/cancel' })
    expect(res).toEqual({ status: 'cancelling' })
  })

  it('listSteps GETs the run steps', async () => {
    const cap = captureAll()
    const steps = await api.listSteps('run_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/workflow-runs/run_1/steps' })
    expect(steps[0]).toMatchObject({ id: 'step_1', state: 'succeeded' })
  })

  // ---- orchestration ----
  it('getApproval GETs the approval with votes', async () => {
    const cap = captureAll()
    const ap = await api.getApproval('ap_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orchestration/approvals/ap_1' })
    expect(ap).toMatchObject({ id: 'ap_1', votes: [] })
  })

  it('listApprovalsForRun GETs the run approvals', async () => {
    const cap = captureAll()
    await api.listApprovalsForRun('run_1')
    expect(cap.value).toMatchObject({
      method: 'GET',
      path: '/api/orchestration/workflow-runs/run_1/approvals',
    })
  })

  it('getInstruction GETs a single instruction', async () => {
    const cap = captureAll()
    await api.getInstruction('ins_1')
    expect(cap.value).toMatchObject({
      method: 'GET',
      path: '/api/orchestration/instructions/ins_1',
    })
  })

  it('listInstructionsForChain GETs the chain instructions', async () => {
    const cap = captureAll()
    await api.listInstructionsForChain('ch_1')
    expect(cap.value).toMatchObject({
      method: 'GET',
      path: '/api/orchestration/chains/ch_1/instructions',
    })
  })

  it('listRunSubagents GETs the run subagents (runId maps to the workflow_run_id path)', async () => {
    const cap = captureAll()
    const subs = await api.listRunSubagents('run_1')
    expect(cap.value).toMatchObject({
      method: 'GET',
      path: '/api/orchestration/workflow-runs/run_1/subagents',
    })
    expect(subs[0]).toMatchObject({ id: 'inst_1' })
  })

  it('listDlq GETs the agent DLQ', async () => {
    const cap = captureAll()
    const dlq = await api.listDlq('ag_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/orchestration/agents/ag_1/dlq' })
    expect(dlq[0]).toMatchObject({ stream_entry_id: 'e_1' })
  })

  // ---- agent wakeup config (reshapes) ----
  it('getAgentWakeupConfig reshapes to { wakeupConfig, version }', async () => {
    const cap = captureAll()
    const res = await api.getAgentWakeupConfig('ag_1')
    expect(cap.value).toMatchObject({ method: 'GET', path: '/api/agents/ag_1' })
    expect(res).toEqual({ wakeupConfig: { enabled: true }, version: 2 })
  })

  it('getAgentWakeupConfig defaults an absent wakeup_config to {}', async () => {
    captureAll()
    server.use(
      mswHttp.get('/api/agents/:agid', () => HttpResponse.json({ id: 'ag_1', version: 5 })),
    )
    const res = await api.getAgentWakeupConfig('ag_1')
    expect(res).toEqual({ wakeupConfig: {}, version: 5 })
  })

  it('patchAgentWakeupConfig PATCHes { wakeup_config } with If-Match and returns the bumped version', async () => {
    const cap = captureAll()
    const version = await api.patchAgentWakeupConfig('ag_1', { enabled: false } as never, 2)
    expect(cap.value).toMatchObject({
      method: 'PATCH',
      path: '/api/agents/ag_1',
      ifMatch: '2',
      body: { wakeup_config: { enabled: false } },
    })
    expect(version).toBe(3)
  })
})

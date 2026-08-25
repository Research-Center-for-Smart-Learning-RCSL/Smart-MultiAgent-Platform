import type { ActivityGroupProposalOut } from '@shared/api-client'

let _seq = 0
function seq(): number {
  return ++_seq
}

export function buildUser(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `u_${n}`,
    email: `user${n}@example.com`,
    display_name: `User ${n}`,
    email_verified: true,
    is_admin: false,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildOrg(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `org_${n}`,
    name: `Org ${n}`,
    owner_id: `u_${n}`,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildProject(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `proj_${n}`,
    name: `Project ${n}`,
    org_id: `org_${n}`,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildApiKey(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `key_${n}`,
    provider: 'openai',
    label: `Key ${n}`,
    masked_key: 'sk-****',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildKeyGroup(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `kg_${n}`,
    name: `Key Group ${n}`,
    members: [],
    ...overrides,
  }
}

export function buildAgent(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `agent_${n}`,
    name: `Agent ${n}`,
    project_id: `proj_${n}`,
    system_prompt: '',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildChatroom(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `cr_${n}`,
    name: `Chatroom ${n}`,
    project_id: `proj_${n}`,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function buildWorkflow(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `wf_${n}`,
    name: `Workflow ${n}`,
    project_id: `proj_${n}`,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** A group's live proposal ([R30.41]). Typed, unlike the loose factories above,
 *  because the store and the panel both take it as the wire shape and an
 *  untyped fixture would let a field silently drift out of the model. */
export function buildGroupProposal(
  overrides: Partial<ActivityGroupProposalOut> = {},
): ActivityGroupProposalOut {
  return {
    id: 'p_1',
    chatroom_id: 'c1',
    activation_id: 'act_1',
    activity_type_id: 'at_1',
    member_group_id: 'g1',
    proposer_user_id: 'u_alice',
    payload: { answer: 'ours' },
    status: 'open',
    // A 2-of-3 vote with the proposer's implicit approval already recorded: the
    // ordinary mid-vote state, and the one most assertions want.
    required_approvals: 2,
    approvals: 1,
    rejections: 0,
    undecided: 2,
    voter_count: 3,
    votes: [],
    created_at: null,
    expires_at: null,
    resolved_at: null,
    submission_id: null,
    ...overrides,
  }
}

export function buildWorkflowRun(overrides: Record<string, unknown> = {}) {
  const n = seq()
  return {
    id: `run_${n}`,
    workflow_id: `wf_${n}`,
    status: 'completed',
    started_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

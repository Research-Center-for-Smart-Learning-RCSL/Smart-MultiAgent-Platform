import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../../tests/utils'
import ApprovalCard from '../ApprovalCard.vue'
import type { ApprovalWithVotes } from '../../types'

const APPROVAL: ApprovalWithVotes = {
  id: 'approval_1',
  workflow_run_id: 'run_1',
  mode: 'majority',
  leader_agent_id: 'agent_1',
  approver_agent_ids: ['agent_1', 'agent_2'],
  timeout_seconds: 300,
  state: 'pending',
  started_at: '2026-01-01T00:00:00Z',
  ended_at: null,
  votes: [],
}

describe('ApprovalCard', () => {
  it('renders with the real SCard flat/sm classes, not the nonexistent surface/compact ones', async () => {
    const wrapper = await renderView(ApprovalCard, {
      props: { approval: APPROVAL, agentNames: {} },
    })
    const card = wrapper.find('.s-card')
    expect(card.exists()).toBe(true)
    expect(card.classes()).toContain('s-card--flat')
    expect(card.classes()).toContain('s-card--pad-sm')
    expect(card.classes()).not.toContain('s-card--surface')
    expect(card.classes()).not.toContain('s-card--pad-compact')
  })
})

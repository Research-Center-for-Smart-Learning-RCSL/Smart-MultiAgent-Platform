import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useOrchestrationStore } from '../orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'

const ROOM = 'room_1'
// 1h after the fixed start → well past the 300s default grace.
const PAST_GRACE = Date.parse('2026-01-01T01:00:00Z')

function approval(overrides: Partial<ApprovalWithVotes> = {}): ApprovalWithVotes {
  return {
    id: 'a1',
    workflow_run_id: 'run_1',
    mode: 'majority',
    leader_agent_id: 'agent_1',
    approver_agent_ids: ['agent_1'],
    timeout_seconds: 300,
    state: 'pending',
    started_at: '2026-01-01T00:00:00Z',
    ended_at: null,
    votes: [],
    ...overrides,
  }
}

describe('orchestration store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('upserts, resolves and removes approval cards', () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())
    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(1)

    s.resolveApproval(ROOM, 'a1', 'approved')
    expect(s.getApprovalsForRoom(ROOM)[0].state).toBe('approved')

    s.removeApproval(ROOM, 'a1')
    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(0)
  })

  it('reconcile removes a card the server reports absent (AC-9)', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())

    const fetcher = vi.fn().mockResolvedValue(null)
    await s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    expect(fetcher).toHaveBeenCalledWith('a1')
    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(0)
  })

  it('reconcile transitions a card the server reports resolved', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())

    const fetcher = vi.fn().mockResolvedValue(approval({ state: 'approved' }))
    await s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    expect(s.getApprovalsForRoom(ROOM)[0].state).toBe('approved')
  })

  it('reconcile leaves a card within its grace window untouched', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())

    const fetcher = vi.fn().mockResolvedValue(null)
    // 100s after start, grace is 300s → still fresh, never fetched.
    await s.reconcilePending(ROOM, fetcher, { now: Date.parse('2026-01-01T00:01:40Z') })

    expect(fetcher).not.toHaveBeenCalled()
    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(1)
  })

  it('reconcile keeps a card on a transient (non-404) fetch error', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())

    const fetcher = vi.fn().mockRejectedValue(new Error('network'))
    await s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(1)
    expect(s.getApprovalsForRoom(ROOM)[0].state).toBe('pending')
  })

  it('reconcile replaces a still-pending card with the authoritative DTO (T-4)', async () => {
    const s = useOrchestrationStore()
    // The shape a new client builds from an old event: everything present but
    // the timestamp, which is a deliberate non-date.
    s.upsertApproval(ROOM, approval({ started_at: 'unknown', timeout_seconds: 300 }))

    const server = approval({ started_at: '2026-01-01T00:00:00Z', votes: [
      { approval_id: 'a1', voter_agent_id: 'agent_1', vote: true, rationale: null },
    ] })
    const fetcher = vi.fn().mockResolvedValue(server)
    await s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    const card = s.getApprovalsForRoom(ROOM)[0]
    // The whole DTO, not just the state: the sentinel is the reason this branch
    // exists, and votes accumulated server-side while the card was stale.
    expect(card.started_at).toBe('2026-01-01T00:00:00Z')
    expect(card.votes).toHaveLength(1)
    expect(card.state).toBe('pending')
  })

  it('reconcile drops a late pending response for a card resolved meanwhile (T-4)', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval({ started_at: 'unknown' }))

    let release!: (v: ApprovalWithVotes) => void
    const fetcher = vi.fn().mockReturnValue(
      new Promise<ApprovalWithVotes>((resolve) => { release = resolve }),
    )
    const pass = s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    // An approval.resolved lands while the fetch is still in flight. The
    // response it eventually returns was read before that and is now stale.
    s.resolveApproval(ROOM, 'a1', 'approved')
    release(approval({ started_at: '2026-01-01T00:00:00Z' }))
    await pass

    expect(s.getApprovalsForRoom(ROOM)[0].state).toBe('approved')
  })

  it('reconcile does not remove a card resolved while its fetch was in flight', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval())

    let release!: (v: ApprovalWithVotes | null) => void
    const fetcher = vi.fn().mockReturnValue(
      new Promise<ApprovalWithVotes | null>((resolve) => { release = resolve }),
    )
    const pass = s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    s.resolveApproval(ROOM, 'a1', 'approved')
    release(null)
    await pass

    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(1)
    expect(s.getApprovalsForRoom(ROOM)[0].state).toBe('approved')
  })

  it('reconcile only touches pending cards', async () => {
    const s = useOrchestrationStore()
    s.upsertApproval(ROOM, approval({ state: 'approved' }))

    const fetcher = vi.fn().mockResolvedValue(null)
    await s.reconcilePending(ROOM, fetcher, { now: PAST_GRACE })

    expect(fetcher).not.toHaveBeenCalled()
    expect(s.getApprovalsForRoom(ROOM)).toHaveLength(1)
  })
})

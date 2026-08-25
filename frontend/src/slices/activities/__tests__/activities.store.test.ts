// AC-4: the store keyed by submission id transitions pending -> validated/error
// without dropping richer local data.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useActivitiesStore } from '../stores/activities'
import type { ActivitySubmission, ActivityTypePublic } from '../types'

function submission(over: Partial<ActivitySubmission> = {}): ActivitySubmission {
  return {
    id: 'sub_1',
    activity_type_id: 'at_1',
    chatroom_id: 'c1',
    session_id: 's1',
    attempt_no: 1,
    validation_status: 'validated',
    is_valid: true,
    sub_scores: { accuracy: 0.9 },
    error_class: null,
    latency_ms: 12,
    created_at: null,
    ...over,
  }
}

describe('activities store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('seeds a full outcome from a submission', () => {
    const store = useActivitiesStore()
    store.upsertFromSubmission('c1', submission())
    const outcome = store.getOutcome('c1', 'sub_1')
    expect(outcome).toMatchObject({ status: 'validated', isValid: true, subScores: { accuracy: 0.9 } })
  })

  it('transitions pending -> validated via WS events, keeping known is_valid', () => {
    const store = useActivitiesStore()
    // in-process validators may seed is_valid before the async event.
    store.upsertFromSubmission('c1', submission({ validation_status: 'pending', is_valid: null }))
    store.applyCreated('c1', { submissionId: 'sub_1', activityTypeId: 'at_1', status: 'pending' })
    expect(store.getOutcome('c1', 'sub_1')!.status).toBe('pending')

    store.applyValidated('c1', { submissionId: 'sub_1', status: 'validated' })
    const outcome = store.getOutcome('c1', 'sub_1')!
    expect(outcome.status).toBe('validated')
    expect(outcome.activityTypeId).toBe('at_1') // preserved across the transition
  })

  it('does not downgrade richer is_valid when a created event arrives after the submit response', () => {
    const store = useActivitiesStore()
    store.upsertFromSubmission('c1', submission({ validation_status: 'validated', is_valid: true }))
    // A slightly-late activity.created carrying only a status must not blank is_valid.
    store.applyCreated('c1', { submissionId: 'sub_1', activityTypeId: 'at_1', status: 'validated' })
    expect(store.getOutcome('c1', 'sub_1')!.isValid).toBe(true)
  })

  it('coerces an unknown status to pending and maps error', () => {
    const store = useActivitiesStore()
    store.applyCreated('c1', { submissionId: 'x', activityTypeId: null, status: 'weird' })
    expect(store.getOutcome('c1', 'x')!.status).toBe('pending')
    store.applyValidated('c1', { submissionId: 'x', status: 'error' })
    expect(store.getOutcome('c1', 'x')!.status).toBe('error')
  })

  it('resets a room in isolation', () => {
    const store = useActivitiesStore()
    store.upsertFromSubmission('c1', submission())
    store.upsertFromSubmission('c2', submission({ id: 'sub_2', chatroom_id: 'c2' }))
    store.resetRoom('c1')
    expect(store.getOutcome('c1', 'sub_1')).toBeUndefined()
    expect(store.getOutcome('c2', 'sub_2')).toBeDefined()
  })

  it('hydrates and clears a room activation without touching other rooms', () => {
    const store = useActivitiesStore()
    store.setActivation('c1', {
      id: 'activation_1',
      activityTypeId: 'at_1',
      startedByUserId: 'user_1',
    })
    store.setActivation('c2', {
      id: 'activation_2',
      activityTypeId: 'at_2',
      startedByUserId: 'user_2',
    })

    expect(store.getActivation('c1')).toMatchObject({ activityTypeId: 'at_1' })
    store.clearActivation('c1', 'activation_1')
    expect(store.getActivation('c1')).toBeNull()
    expect(store.getActivation('c2')).toMatchObject({ activityTypeId: 'at_2' })
  })

  it('carries activityType through both input shapes (Q-1)', () => {
    const store = useActivitiesStore()
    const publicType: ActivityTypePublic = {
      id: 'at_1',
      key: 'demo',
      name: 'Demo',
      payload_schema: { type: 'object' },
    }

    // HTTP shape (ActivityActivationOut): snake_case, `activity_type`.
    store.setActivation('c1', {
      id: 'act_1',
      chatroom_id: 'c1',
      activity_type_id: 'at_1',
      started_by_user_id: 'u1',
      status: 'active',
      created_at: null,
      ended_at: null,
      activity_type: publicType,
    })
    expect(store.getActivation('c1')?.activityType).toEqual(publicType)

    // WS ids-only view (ActivationView): already camelCase, `activityType`.
    store.setActivation('c2', {
      id: 'act_2',
      activityTypeId: 'at_2',
      startedByUserId: 'u2',
      activityType: publicType,
    })
    expect(store.getActivation('c2')?.activityType).toEqual(publicType)
  })

  it('defaults activityType to null when the HTTP shape omits it', () => {
    const store = useActivitiesStore()
    store.setActivation('c1', {
      id: 'act_1',
      chatroom_id: 'c1',
      activity_type_id: 'at_1',
      started_by_user_id: 'u1',
      status: 'active',
      created_at: null,
      ended_at: null,
    })
    expect(store.getActivation('c1')?.activityType).toBeNull()
  })
})

describe('group proposal state ([R30.42])', () => {
  beforeEach(() => setActivePinia(createPinia()))

  const round = {
    activationId: 'act_1',
    proposals: [
      {
        id: 'p_1',
        chatroom_id: 'c1',
        activation_id: 'act_1',
        activity_type_id: 'at_1',
        member_group_id: 'g1',
        proposer_user_id: 'u1',
        payload: { answer: 'ours' },
        status: 'open',
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
      },
    ],
    eligibleGroups: [{ id: 'g1', name: 'Group A' }],
  }

  it('adopts a round wholesale, replacing rather than merging', () => {
    // The read IS the authorization boundary: a proposal it stopped returning
    // is one this caller may no longer see, and keeping it would leave a card
    // the server has stopped vouching for.
    const store = useActivitiesStore()
    store.setRound('c1', round)
    store.setRound('c1', { ...round, proposals: [] })

    expect(store.getProposalRoom('c1')?.proposals).toEqual({})
  })

  it('never inserts a proposal it only heard about over the room channel', () => {
    const store = useActivitiesStore()
    store.setRound('c1', round)

    store.applyProposalEvent('c1', {
      proposalId: 'p_other',
      memberGroupId: 'g2',
      status: 'open',
      approvals: 1,
    })

    expect(Object.keys(store.getProposalRoom('c1')?.proposals ?? {})).toEqual(['p_1'])
    expect(store.getProposalRoom('c1')?.unseenGroupIds).toEqual(['g2'])
  })

  it('records an unseen group once, however many events it sends', () => {
    const store = useActivitiesStore()
    store.setRound('c1', round)

    for (const status of ['opened', 'voted']) {
      store.applyProposalEvent('c1', {
        proposalId: `p_${status}`,
        memberGroupId: 'g2',
        status: 'open',
      })
    }

    expect(store.getProposalRoom('c1')?.unseenGroupIds).toEqual(['g2'])
  })

  it('keeps every count the expiry sweep did not send', () => {
    const store = useActivitiesStore()
    store.setRound('c1', round)

    store.applyProposalEvent('c1', {
      proposalId: 'p_1',
      memberGroupId: 'g1',
      status: 'expired',
    })

    const stored = store.getProposalRoom('c1')?.proposals.p_1
    expect(stored?.status).toBe('expired')
    expect(stored?.approvals).toBe(1)
    expect(stored?.undecided).toBe(2)
    expect(stored?.required_approvals).toBe(2)
  })

  it('adopts a zero count the broadcast did send', () => {
    // `?? known` and not `|| known`: 0 rejections is a real answer and the one
    // a group is most often at.
    const store = useActivitiesStore()
    store.setRound('c1', round)

    store.applyProposalEvent('c1', {
      proposalId: 'p_1',
      memberGroupId: 'g1',
      status: 'open',
      undecided: 0,
      approvals: 3,
    })

    expect(store.getProposalRoom('c1')?.proposals.p_1?.undecided).toBe(0)
  })

  it('clears the group state with the room and with the session', () => {
    const store = useActivitiesStore()
    store.setRound('c1', round)
    store.resetRoom('c1')
    expect(store.getProposalRoom('c1')).toBeUndefined()

    store.setRound('c2', round)
    store.clearAll()
    expect(store.getProposalRoom('c2')).toBeUndefined()
  })
})

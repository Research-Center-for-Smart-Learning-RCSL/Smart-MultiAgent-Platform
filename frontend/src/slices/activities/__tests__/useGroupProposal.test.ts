// The group-submission composable ([R30.41], [R30.42]).
//
// The claim worth testing hardest is the one the room broadcast makes easy to
// get wrong: `activity.proposal.*` reaches EVERY participant, including members
// of groups this caller may not read. A client that trusted the event would
// render another group's vote from a payload that carries no evidence the
// caller is entitled to it.

import { defineComponent, h, nextTick } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderView } from '../../../../tests/utils'
import { useActivitiesStore } from '../stores/activities'
import { useGroupProposal, type UseGroupProposal } from '../composables/useGroupProposal'
import type { ActivityGroupProposal } from '../types'

const listGroupProposalsMock = vi.hoisted(() => vi.fn())
const createGroupProposalMock = vi.hoisted(() => vi.fn())
const voteOnGroupProposalMock = vi.hoisted(() => vi.fn())
const withdrawGroupProposalMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  listGroupProposals: listGroupProposalsMock,
  createGroupProposal: createGroupProposalMock,
  voteOnGroupProposal: voteOnGroupProposalMock,
  withdrawGroupProposal: withdrawGroupProposalMock,
}))

const ROOM = 'c1'
const ACTIVATION = 'act_1'
const MY_GROUP = 'g_mine'
const OTHER_GROUP = 'g_theirs'

function proposal(over: Partial<ActivityGroupProposal> = {}): ActivityGroupProposal {
  return {
    id: 'p_1',
    chatroom_id: ROOM,
    activation_id: ACTIVATION,
    activity_type_id: 'at_1',
    member_group_id: MY_GROUP,
    proposer_user_id: 'u_alice',
    payload: { answer: 'ours' },
    status: 'open',
    required_approvals: 2,
    approvals: 1,
    rejections: 0,
    undecided: 2,
    voter_count: 3,
    votes: [{ user_id: 'u_alice', approve: true, created_at: null }],
    created_at: null,
    expires_at: null,
    resolved_at: null,
    submission_id: null,
    ...over,
  }
}

/** Mount the composable inside a real component so its watchers run. */
async function mountComposable(
  opts: { viewerUserId?: string | null; isCreator?: boolean } = {},
): Promise<{ group: UseGroupProposal; store: ReturnType<typeof useActivitiesStore> }> {
  let group!: UseGroupProposal
  let store!: ReturnType<typeof useActivitiesStore>
  const Harness = defineComponent({
    setup() {
      store = useActivitiesStore()
      group = useGroupProposal({
        chatroomId: () => ROOM,
        activationId: () => ACTIVATION,
        activityTypeId: () => 'at_1',
        viewerUserId: () => opts.viewerUserId ?? 'u_bob',
        isCreator: () => opts.isCreator ?? false,
      })
      return () => h('div')
    },
  })
  await renderView(Harness)
  await flushPromises()
  return { group, store }
}

beforeEach(() => {
  listGroupProposalsMock.mockResolvedValue({
    items: [proposal()],
    eligible_groups: [{ id: MY_GROUP, name: 'Group A' }],
  })
})

afterEach(() => {
  listGroupProposalsMock.mockReset()
  createGroupProposalMock.mockReset()
  voteOnGroupProposalMock.mockReset()
  withdrawGroupProposalMock.mockReset()
})

describe('seeding a round', () => {
  it('adopts the server-narrowed read for this activation', async () => {
    const { group } = await mountComposable()

    expect(listGroupProposalsMock).toHaveBeenCalledWith(ROOM, ACTIVATION)
    expect(group.openProposal.value?.id).toBe('p_1')
    expect(group.canPropose.value).toBe(true)
    expect(group.groupName(MY_GROUP)).toBe('Group A')
  })

  it('offers no group mode when the caller belongs to none', async () => {
    // A room member in no bound group gets an empty list, not a 403 (AC-12) —
    // and that is exactly the caller who must not be shown a picker.
    listGroupProposalsMock.mockResolvedValue({ items: [], eligible_groups: [] })

    const { group } = await mountComposable()

    expect(group.canPropose.value).toBe(false)
    expect(group.errorMessage.value).toBeNull()
  })

  it('reports a failed read rather than degrading it into "no group mode"', async () => {
    const { ApiError } = await import('@shared/errors')
    listGroupProposalsMock.mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'nope', status: 500 }),
    )

    const { group } = await mountComposable()

    expect(group.errorMessage.value).not.toBeNull()
  })

  it('ignores a proposal for a group the caller is not in', async () => {
    // The room creator's listing covers every bound group, but the card is the
    // participant surface: a proposal they hold no ballot on is not "theirs".
    listGroupProposalsMock.mockResolvedValue({
      items: [proposal({ id: 'p_other', member_group_id: OTHER_GROUP })],
      eligible_groups: [{ id: MY_GROUP, name: 'Group A' }],
    })

    const { group } = await mountComposable()

    expect(group.openProposal.value).toBeNull()
  })

  it('treats a resolved proposal as no longer the open one', async () => {
    listGroupProposalsMock.mockResolvedValue({
      items: [proposal({ status: 'accepted' })],
      eligible_groups: [{ id: MY_GROUP, name: 'Group A' }],
    })

    const { group } = await mountComposable()

    expect(group.openProposal.value).toBeNull()
  })
})

describe('the viewer own decision', () => {
  it('reads the caller own vote off the per-person record', async () => {
    const { group } = await mountComposable({ viewerUserId: 'u_alice' })

    expect(group.myVote.value).toBe(true)
    expect(group.isProposer.value).toBe(true)
  })

  it('is undecided for a pinned voter who has not voted', async () => {
    const { group } = await mountComposable({ viewerUserId: 'u_bob' })

    expect(group.myVote.value).toBeNull()
    expect(group.isProposer.value).toBe(false)
  })

  it('is undecided when no identity has resolved, which disables the controls', async () => {
    const { group } = await mountComposable({ viewerUserId: null })

    expect(group.myVote.value).toBeNull()
    expect(group.isProposer.value).toBe(false)
  })
})

describe('room broadcasts are counts, not authorization ([R30.42])', () => {
  it('applies counts to a proposal the read already returned', async () => {
    const { group, store } = await mountComposable()

    store.applyProposalEvent(ROOM, {
      proposalId: 'p_1',
      memberGroupId: MY_GROUP,
      status: 'open',
      approvals: 2,
      rejections: 0,
      undecided: 1,
      requiredApprovals: 2,
      voterCount: 3,
    })
    await nextTick()

    expect(group.openProposal.value?.approvals).toBe(2)
    expect(listGroupProposalsMock).toHaveBeenCalledTimes(1)
  })

  it('never inserts another group proposal, and does not refetch for it', async () => {
    // The defect this prevents: a student seeing that another group is voting,
    // and on what, purely because the broadcast reached their socket.
    const { group, store } = await mountComposable()

    store.applyProposalEvent(ROOM, {
      proposalId: 'p_other',
      memberGroupId: OTHER_GROUP,
      status: 'open',
      approvals: 1,
    })
    await flushPromises()

    expect(group.proposals.value.map((p) => p.id)).toEqual(['p_1'])
    expect(listGroupProposalsMock).toHaveBeenCalledTimes(1)
  })

  it('re-reads when a proposal opens in a group the caller does belong to', async () => {
    // A groupmate proposed. The event alone is not enough to render the card —
    // it carries no payload — so the server is asked, and it re-authorizes.
    const { store } = await mountComposable()
    listGroupProposalsMock.mockResolvedValue({
      items: [proposal({ id: 'p_2' })],
      eligible_groups: [{ id: MY_GROUP, name: 'Group A' }],
    })

    store.applyProposalEvent(ROOM, {
      proposalId: 'p_2',
      memberGroupId: MY_GROUP,
      status: 'open',
      approvals: 1,
    })
    await flushPromises()

    expect(listGroupProposalsMock).toHaveBeenCalledTimes(2)
  })

  it('re-reads for any bound group when the caller is the room creator', async () => {
    const { store } = await mountComposable({ isCreator: true })

    store.applyProposalEvent(ROOM, {
      proposalId: 'p_other',
      memberGroupId: OTHER_GROUP,
      status: 'open',
      approvals: 1,
    })
    await flushPromises()

    expect(listGroupProposalsMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the last known counts when the expiry sweep sends a status alone', async () => {
    // The worker holds no tally and sends none. Folding its absent counts in as
    // 0 would report a settled vote as unanimous abstention.
    const { group, store } = await mountComposable()

    store.applyProposalEvent(ROOM, {
      proposalId: 'p_1',
      memberGroupId: MY_GROUP,
      status: 'expired',
    })
    await nextTick()

    const stored = group.proposals.value.find((p) => p.id === 'p_1')
    expect(stored?.status).toBe('expired')
    expect(stored?.approvals).toBe(1)
    expect(stored?.voter_count).toBe(3)
  })
})

describe('mutations', () => {
  it('adopts the tally the vote returned, including an acceptance', async () => {
    const { group } = await mountComposable()
    voteOnGroupProposalMock.mockResolvedValue(
      proposal({ status: 'accepted', approvals: 2, submission_id: 'sub_1' }),
    )

    await group.vote('p_1', true)

    expect(group.openProposal.value).toBeNull()
    expect(group.proposals.value[0]?.submission_id).toBe('sub_1')
  })

  it('refetches rather than showing a stale count when the proposal settled first (§7)', async () => {
    const { ApiError } = await import('@shared/errors')
    const { group } = await mountComposable()
    voteOnGroupProposalMock.mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'already resolved', status: 409 }),
    )

    await group.vote('p_1', true)
    await flushPromises()

    expect(group.errorMessage.value).not.toBeNull()
    expect(listGroupProposalsMock).toHaveBeenCalledTimes(2)
  })

  it('does not refetch on an ordinary failure, which changed nothing server-side', async () => {
    const { ApiError } = await import('@shared/errors')
    const { group } = await mountComposable()
    voteOnGroupProposalMock.mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'boom', status: 500 }),
    )

    await group.vote('p_1', true)
    await flushPromises()

    expect(listGroupProposalsMock).toHaveBeenCalledTimes(1)
  })

  it('sends the proposal with the group the caller chose', async () => {
    const { group } = await mountComposable()
    createGroupProposalMock.mockResolvedValue(proposal({ id: 'p_3' }))

    await group.propose(MY_GROUP, { answer: 'ours' })

    expect(createGroupProposalMock).toHaveBeenCalledWith(ROOM, {
      activity_type_id: 'at_1',
      member_group_id: MY_GROUP,
      payload: { answer: 'ours' },
    })
  })

  it('refuses to run two mutations at once', async () => {
    const { deferred } = await import('../../../../tests/utils')
    const gate = deferred<ActivityGroupProposal>()
    const { group } = await mountComposable()
    voteOnGroupProposalMock.mockReturnValue(gate.promise)

    const first = group.vote('p_1', true)
    await group.vote('p_1', false)
    gate.resolve(proposal({ approvals: 2 }))
    await first

    expect(voteOnGroupProposalMock).toHaveBeenCalledTimes(1)
  })
})

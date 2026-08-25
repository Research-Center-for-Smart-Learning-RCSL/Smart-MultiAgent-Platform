// The one card a group sees while it votes ([R30.41]). Component assertions
// read raw i18n keys (the harness mounts no bundle, BOARD.md FU-4), so the
// threshold's actual wording is asserted in `i18n.group.test.ts`; what is
// asserted here is which controls exist in which state.

import { describe, expect, it } from 'vitest'
import { buildGroupProposal, renderView } from '../../../../tests/utils'
import GroupProposalCard from '../components/GroupProposalCard.vue'
import type { ActivityGroupProposal } from '../types'

function proposal(over: Partial<ActivityGroupProposal> = {}): ActivityGroupProposal {
  return buildGroupProposal({
    payload: { case: 'A shared scenario', hat_white: 'facts' },
    required_approvals: 3,
    approvals: 2,
    voter_count: 4,
    ...over,
  })
}

function mount(over: Record<string, unknown> = {}) {
  return renderView(GroupProposalCard, {
    props: {
      proposal: proposal(),
      groupName: 'Group A',
      myVote: null,
      isProposer: false,
      pending: false,
      canVote: true,
      ...over,
    },
  })
}

describe('while the vote is open', () => {
  it('offers both decisions to a pinned voter who has not voted', async () => {
    const wrapper = await mount()

    expect(wrapper.find('[data-testid="group-proposal-approve"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="group-proposal-reject"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="group-proposal-threshold"]').exists()).toBe(true)
  })

  it('shows the proposed answer, since its readers are the ones approving it', async () => {
    const wrapper = await mount()

    expect(wrapper.text()).toContain('A shared scenario')
    expect(wrapper.text()).toContain('facts')
  })

  it('leaves out a field the group did not fill in', async () => {
    const wrapper = await mount({
      proposal: proposal({ payload: { case: 'kept', hat_red: '', hat_black: null } }),
    })

    expect(wrapper.text()).toContain('kept')
    expect(wrapper.text()).not.toContain('hat_red')
    expect(wrapper.text()).not.toContain('hat_black')
  })

  it('replaces the buttons with the caller decision once they have voted', async () => {
    // A vote is final: one row per pinned voter, and the server refuses a
    // second. Leaving the buttons up would only offer a click that 409s.
    const wrapper = await mount({ myVote: false })

    expect(wrapper.find('[data-testid="group-proposal-approve"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="group-proposal-reject"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="group-proposal-my-vote"]').text()).toContain(
      'activities.group.myVoteRejected',
    )
  })

  it('offers withdraw to the proposer only', async () => {
    const mine = await mount({ isProposer: true })
    const theirs = await mount({ isProposer: false })

    expect(mine.find('[data-testid="group-proposal-withdraw"]').exists()).toBe(true)
    expect(theirs.find('[data-testid="group-proposal-withdraw"]').exists()).toBe(false)
  })

  it('disables voting for a session with no resolved identity', async () => {
    const wrapper = await mount({ canVote: false })

    expect(wrapper.find('[data-testid="group-proposal-approve"]').attributes('disabled')).toBeDefined()
  })

  it('emits the decision the caller clicked', async () => {
    const wrapper = await mount()

    await wrapper.find('[data-testid="group-proposal-reject"]').trigger('click')

    expect(wrapper.emitted('vote')?.[0]).toEqual([false])
  })
})

describe('once it has settled', () => {
  it.each(['accepted', 'rejected', 'withdrawn', 'expired'] as const)(
    'shows no controls and no threshold for a %s proposal',
    async (status) => {
      const wrapper = await mount({ proposal: proposal({ status }) })

      expect(wrapper.find('[data-testid="group-proposal-approve"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="group-proposal-withdraw"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="group-proposal-threshold"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="group-proposal-status"]').text()).toContain(
        `activities.group.status${status[0]!.toUpperCase()}${status.slice(1)}`,
      )
    },
  )

  it('treats an unrecognized status as still open rather than hiding a live vote', async () => {
    const wrapper = await mount({ proposal: proposal({ status: 'something_new' }) })

    expect(wrapper.find('[data-testid="group-proposal-threshold"]').exists()).toBe(true)
  })

  it('offers another attempt when the group is allowed one', async () => {
    const wrapper = await mount({ proposal: proposal({ status: 'rejected' }), canRetry: true })

    await wrapper.find('[data-testid="group-proposal-retry"]').trigger('click')

    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('offers none after an acceptance, which is the round answer', async () => {
    // A second proposal here is not blocked by the partial unique -- that bars
    // only concurrent OPEN ones -- so it would land as a duplicate attempt.
    const wrapper = await mount({ proposal: proposal({ status: 'accepted' }), canRetry: false })

    expect(wrapper.find('[data-testid="group-proposal-retry"]').exists()).toBe(false)
  })
})

describe('naming the group', () => {
  it('names the group whose answer this is', async () => {
    const wrapper = await mount()

    expect(wrapper.text()).toContain('activities.group.cardTitle')
  })

  it('falls back to an unnamed title rather than printing a uuid', async () => {
    const wrapper = await mount({ groupName: null })

    expect(wrapper.text()).toContain('activities.group.cardTitleUnnamed')
    expect(wrapper.text()).not.toContain('g1')
  })
})

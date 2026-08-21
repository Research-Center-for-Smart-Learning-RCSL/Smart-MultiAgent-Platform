import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import InboxInvitesView from '../views/InboxInvitesView.vue'

describe('InboxInvitesView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(InboxInvitesView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows empty state when there are no invites', async () => {
    const wrapper = await renderView(InboxInvitesView)
    await flushPromises()
    // MSW returns [] for /api/invites/inbox, so the empty message should display
    expect(wrapper.find('ul').exists()).toBe(false)
  })

  // F-27 / Q-10: a skeleton may never be taller than the shortest settled state
  // its branch can produce. Three 120px rects in a 12px-gap column is 384px,
  // and this branch settles to an SEmptyState around 176px, so the page used to
  // jump upward under the cursor when an empty result landed.
  it('keeps the skeleton no taller than the empty state it settles to', async () => {
    // Deliberately no flushPromises — the query is still in flight here.
    const wrapper = await renderView(InboxInvitesView)

    const skeletons = wrapper.findAll('.s-skeleton')
    expect(skeletons).toHaveLength(1)
    expect((skeletons[0]!.element as HTMLElement).style.height).toBe('120px')
  })
})

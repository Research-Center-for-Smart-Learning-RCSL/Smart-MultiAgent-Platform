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
})

import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import GuestLandingView from '../views/GuestLandingView.vue'

const routes = [
  {
    path: '/g/:chatroomId/:guestToken',
    name: 'conversation.guest',
    component: GuestLandingView,
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
]

describe('GuestLandingView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the enrollment card with a live region on mount', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    // The card mounts and fires enrollGuest; its content area announces state
    // changes via an aria-live region regardless of the resolved state.
    expect(wrapper.find('.guest-landing').exists()).toBe(true)
    expect(wrapper.find('[aria-live]').exists()).toBe(true)
  })
})

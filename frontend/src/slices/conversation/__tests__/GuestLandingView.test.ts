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

  it('shows the enrolling state initially', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.find('.guest-landing').exists()).toBe(true)
    // The component starts in 'enrolling' state and fires enrollGuest on mount.
    expect(wrapper.find('p').exists()).toBe(true)
  })
})

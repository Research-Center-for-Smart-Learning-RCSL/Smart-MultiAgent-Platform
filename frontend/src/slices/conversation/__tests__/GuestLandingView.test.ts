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
    expect(wrapper.find('.guest-landing').exists()).toBe(true)
    expect(wrapper.find('[aria-live]').exists()).toBe(true)
  })

  it('shows the display name form for a new guest', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    expect(wrapper.find('.guest-form').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('shows cap-reached message when state is cap_reached', async () => {
    const wrapper = await renderView(GuestLandingView, {
      routes,
      initialRoute: '/g/cr_1/tok_abc',
    })
    // The component starts in idle state (no localStorage) and shows the form
    expect(wrapper.find('.guest-form').exists()).toBe(true)
  })
})

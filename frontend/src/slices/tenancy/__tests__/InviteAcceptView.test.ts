import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { renderView } from '../../../../tests/utils'
import InviteAcceptView from '../views/InviteAcceptView.vue'

describe('InviteAcceptView', () => {
  afterEach(() => {
    window.location.hash = ''
  })

  it('renders without errors', async () => {
    const wrapper = await renderView(InviteAcceptView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows a link back to the invitations inbox', async () => {
    const wrapper = await renderView(InviteAcceptView)
    expect(wrapper.find('a').exists()).toBe(true)
  })

  describe('with a token in the hash', () => {
    beforeEach(() => {
      window.location.hash = '#token=abc123'
    })

    it('accepts the invite and shows success (no error state)', async () => {
      const wrapper = await renderView(InviteAcceptView)
      // allow the onMounted POST (mocked → state=accepted) to resolve
      await new Promise((r) => setTimeout(r, 10))
      await wrapper.vm.$nextTick()
      // success branch renders a plain <p> (no `.error` class)
      expect(wrapper.find('.error').exists()).toBe(false)
    })
  })

  it('shows failure when no token is present', async () => {
    const wrapper = await renderView(InviteAcceptView)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.error').exists()).toBe(true)
  })
})

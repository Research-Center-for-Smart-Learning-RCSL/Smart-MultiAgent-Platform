import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ProfileView from '../views/ProfileView.vue'

describe('ProfileView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProfileView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the display name field and email label', async () => {
    const wrapper = await renderView(ProfileView)
    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('dl').exists()).toBe(true)
  })

  it('renders the connections section with a Connect action when no provider is linked', async () => {
    const wrapper = await renderView(ProfileView)
    await flushPromises()
    expect(wrapper.text()).toContain('identity.profile.connections.title')
    expect(wrapper.text()).toContain('identity.profile.connections.link')
  })
})

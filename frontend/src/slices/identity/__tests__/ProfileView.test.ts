import { beforeEach, describe, it, expect, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ProfileView from '../views/ProfileView.vue'

const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))

describe('ProfileView', () => {
  beforeEach(() => vi.clearAllMocks())
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

  it('confirms a profile save with a transient toast', async () => {
    const wrapper = await renderView(ProfileView)
    await flushPromises()
    await wrapper.get('input[type="text"]').setValue('Grace')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sonner.success).toHaveBeenCalledWith('identity.profile.saved', expect.any(Object))
    expect(wrapper.find('.s-alert--success').exists()).toBe(false)
  })
})

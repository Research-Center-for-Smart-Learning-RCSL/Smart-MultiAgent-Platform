import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChangeEmailView from '../views/ChangeEmailView.vue'

describe('ChangeEmailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChangeEmailView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows email and password fields', async () => {
    const wrapper = await renderView(ChangeEmailView)
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
  })

  it('shows current email label', async () => {
    const wrapper = await renderView(ChangeEmailView)
    expect(wrapper.find('dl').exists()).toBe(true)
  })
})

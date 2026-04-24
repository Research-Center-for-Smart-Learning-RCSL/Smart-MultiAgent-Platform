import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChangePasswordView from '../views/ChangePasswordView.vue'

describe('ChangePasswordView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChangePasswordView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows current and new password fields', async () => {
    const wrapper = await renderView(ChangePasswordView)
    const inputs = wrapper.findAll('input[type="password"]')
    expect(inputs.length).toBe(2)
  })

  it('submit button is present', async () => {
    const wrapper = await renderView(ChangePasswordView)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})

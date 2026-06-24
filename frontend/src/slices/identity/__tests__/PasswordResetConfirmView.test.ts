import { describe, it, expect, beforeEach } from 'vitest'
import { renderView } from '../../../../tests/utils'
import PasswordResetConfirmView from '../views/PasswordResetConfirmView.vue'

describe('PasswordResetConfirmView', () => {
  beforeEach(() => {
    window.location.hash = ''
  })

  it('renders without errors', async () => {
    const wrapper = await renderView(PasswordResetConfirmView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows password input fields when token is present', async () => {
    window.location.hash = '#token=test-token'
    const wrapper = await renderView(PasswordResetConfirmView)
    const inputs = wrapper.findAll('input[type="password"]')
    expect(inputs.length).toBe(2)
  })

  it('shows invalid link message when no token is provided', async () => {
    const wrapper = await renderView(PasswordResetConfirmView)
    expect(wrapper.text()).toContain('identity.passwordReset.invalidLink')
  })
})

import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import PasswordResetConfirmView from '../views/PasswordResetConfirmView.vue'

describe('PasswordResetConfirmView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(PasswordResetConfirmView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows password input field', async () => {
    const wrapper = await renderView(PasswordResetConfirmView)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
  })

  it('shows error when submitting without token', async () => {
    const wrapper = await renderView(PasswordResetConfirmView)
    await wrapper.find('input[type="password"]').setValue('newpassword123')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.error').exists()).toBe(true)
    expect(wrapper.find('.error').text()).toBe('missing-token')
  })
})

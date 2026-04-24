import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import PasswordResetRequestView from '../views/PasswordResetRequestView.vue'

describe('PasswordResetRequestView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(PasswordResetRequestView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows email input and submit button', async () => {
    const wrapper = await renderView(PasswordResetRequestView)
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })

  it('shows success message after submit', async () => {
    server.use(
      http.post('/api/auth/password-reset/request', () => new HttpResponse(null, { status: 204 })),
    )
    const wrapper = await renderView(PasswordResetRequestView)
    await wrapper.find('input[type="email"]').setValue('user@example.com')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
  })
})

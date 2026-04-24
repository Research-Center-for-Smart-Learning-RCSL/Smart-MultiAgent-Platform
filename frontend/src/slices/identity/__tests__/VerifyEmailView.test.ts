import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import VerifyEmailView from '../views/VerifyEmailView.vue'

describe('VerifyEmailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(VerifyEmailView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows failure when no token is provided', async () => {
    const wrapper = await renderView(VerifyEmailView)
    await flushPromises()
    expect(wrapper.find('.error').exists()).toBe(true)
  })

  it('shows success when token is valid', async () => {
    server.use(
      http.post('/api/auth/verify-email', () => new HttpResponse(null, { status: 204 })),
    )
    const wrapper = await renderView(VerifyEmailView, {
      initialRoute: '/verify-email?token=valid-token',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('identity.verifyEmail.success')
  })
})

import { describe, it, expect, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import VerifyEmailView from '../views/VerifyEmailView.vue'

describe('VerifyEmailView', () => {
  // The token rides in the URL fragment, not the query string (SEC-8).
  beforeEach(() => {
    window.location.hash = ''
  })

  it('renders without errors', async () => {
    const wrapper = await renderView(VerifyEmailView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows failure when no token is provided', async () => {
    const wrapper = await renderView(VerifyEmailView)
    await flushPromises()
    expect(wrapper.text()).toContain('identity.verifyEmail.invalidToken')
  })

  it('shows success when token is valid', async () => {
    server.use(
      http.post('/api/auth/verify-email', () => new HttpResponse(null, { status: 204 })),
    )
    window.location.hash = '#token=valid-token'
    const wrapper = await renderView(VerifyEmailView)
    await flushPromises()
    expect(wrapper.text()).toContain('identity.verifyEmail.success')
  })
})

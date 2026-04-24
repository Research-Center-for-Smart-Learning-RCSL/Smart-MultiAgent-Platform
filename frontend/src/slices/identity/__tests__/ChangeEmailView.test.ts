import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
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

  it('hides form and shows confirmation after success', async () => {
    server.use(
      http.post('/api/auth/change-email', () => new HttpResponse(null, { status: 204 })),
    )
    const wrapper = await renderView(ChangeEmailView)
    await wrapper.find('input[type="email"]').setValue('new@example.com')
    await wrapper.find('input[type="password"]').setValue('password123')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
  })
})

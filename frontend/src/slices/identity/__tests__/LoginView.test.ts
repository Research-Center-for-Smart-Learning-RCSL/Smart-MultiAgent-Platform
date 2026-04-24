import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import LoginView from '../views/LoginView.vue'

describe('LoginView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(LoginView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows email and password fields', async () => {
    const wrapper = await renderView(LoginView)
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
  })

  it('shows pending verify message when query param is set', async () => {
    const wrapper = await renderView(LoginView, {
      initialRoute: '/login?pendingVerify=1',
    })
    expect(wrapper.text()).toContain('identity.verifyEmail.verifying')
  })

  it('submit button is present and enabled', async () => {
    const wrapper = await renderView(LoginView)
    const btn = wrapper.find('button[type="submit"]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
  })
})

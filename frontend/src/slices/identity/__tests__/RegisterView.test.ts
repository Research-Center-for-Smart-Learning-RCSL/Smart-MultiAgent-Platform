import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import RegisterView from '../views/RegisterView.vue'

describe('RegisterView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.exists()).toBe(true)
  })

  it('shows email and password fields', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[type="password"]').exists()).toBe(true)
  })

  it('renders no CAPTCHA widget when the backend reports mode=off', async () => {
    // captcha-config mock returns provider=off (tests/mocks/handlers.ts), so the
    // widget slot must stay empty — the old paste-box input is gone.
    const wrapper = await renderView(RegisterView)
    await new Promise((r) => setTimeout(r, 0))
    expect(wrapper.find('[data-testid="captcha-widget"]').exists()).toBe(false)
  })

  it('has a link back to login', async () => {
    const wrapper = await renderView(RegisterView)
    expect(wrapper.find('a').exists()).toBe(true)
  })
})

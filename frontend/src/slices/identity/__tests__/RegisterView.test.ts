import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
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

  it('requires a captcha token when mode=on even if the provider is unrenderable', async () => {
    // Misconfiguration edge: captcha enforced (mode=on) but the provider is one
    // the widget can't render, so toCaptchaConfig coerces provider to 'off' and no
    // widget shows. Submit must still be gated on mode -- blocked here with a
    // captcha error, not posting an empty token the backend would reject.
    server.use(
      http.get('/api/auth/captcha-config', () =>
        HttpResponse.json({ mode: 'on', provider: 'off', sitekey: '' }),
      ),
    )
    let registerCalled = false
    server.use(
      http.post('/api/auth/register', () => {
        registerCalled = true
        return new HttpResponse(null, { status: 202 })
      }),
    )

    const wrapper = await renderView(RegisterView)
    await flushPromises()
    await wrapper.find('input[type="email"]').setValue('user@example.com')
    await wrapper.find('input[type="password"]').setValue('SuperSecret1!')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(registerCalled).toBe(false)
    expect(wrapper.find('p.field-error[role="alert"]').exists()).toBe(true)
  })
})

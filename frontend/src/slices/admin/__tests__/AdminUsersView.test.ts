import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminUsersView from '../views/AdminUsersView.vue'

const LINKS = {
  set_password_url: 'https://smap.example/reset-password#token=set-me',
  verify_email_url: 'https://smap.example/verify-email#token=verify-me',
  set_password_expires_at: '2026-08-21T10:30:00Z',
  verify_email_expires_at: '2026-08-22T10:00:00Z',
}

describe('AdminUsersView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminUsersView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the search form with input, select, and submit button', async () => {
    const wrapper = await renderView(AdminUsersView)
    expect(wrapper.find('form input[type="text"]').exists()).toBe(true)
    expect(wrapper.find('form select').exists()).toBe(true)
    expect(wrapper.find('form button[type="submit"]').exists()).toBe(true)
  })

  it('displays user rows when data is loaded', async () => {
    server.use(
      http.get('/api/admin/users', () =>
        HttpResponse.json([
          { id: 'u_1', email: 'a@b.com', status: 'active', email_verified: true, created_at: '2026-01-01T00:00:00Z' },
          { id: 'u_2', email: 'c@d.com', status: 'banned', email_verified: false, created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(AdminUsersView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.findAll('tbody tr').length).toBe(2)
  })

  // R6.18: provisioning, and the two links that are the whole point of it.
  it('provisions an account and hands over both activation links', async () => {
    let body: unknown = null
    server.use(
      http.post('/api/admin/users', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(
          {
            user: {
              id: 'u_new',
              email: 'student@example.com',
              display_name: 'Student',
              status: 'pending',
              email_verified: false,
              created_at: '2026-08-21T10:00:00Z',
            },
            activation_links: LINKS,
          },
          { status: 201 },
        )
      }),
    )
    const wrapper = await renderView(AdminUsersView)

    await wrapper.find('.s-page-header__actions button').trigger('click')
    await wrapper.find('#adminCreateUserEmail').setValue('student@example.com')
    await wrapper.find('#adminCreateUserDisplayName').setValue('Student')
    // Submitted through the form rather than by clicking the footer button.
    // The button lives outside the <form> (SModal renders the footer slot as a
    // sibling) and reaches it via the HTML5 `form` attribute, which jsdom does
    // not honour for implicit submission — a click there fires nothing. The real
    // button click is covered in Chromium by e2e/20-onboarding-without-smtp.
    await wrapper.find('#adminCreateUserForm').trigger('submit')

    await vi.waitFor(() => {
      if (!wrapper.find('#adminSetPasswordUrl').exists()) throw new Error('no links dialog yet')
    })
    expect(body).toEqual({ email: 'student@example.com', display_name: 'Student' })

    const setPassword = wrapper.find('#adminSetPasswordUrl').element as HTMLInputElement
    const verify = wrapper.find('#adminVerifyEmailUrl').element as HTMLInputElement
    expect(setPassword.value).toBe(LINKS.set_password_url)
    expect(verify.value).toBe(LINKS.verify_email_url)
    // Labelled separately, because handing over the wrong one is the failure mode.
    expect(setPassword.value).not.toBe(verify.value)
  })

  // No password field exists to leak one, and no response carries one.
  it('offers no password field when provisioning', async () => {
    const wrapper = await renderView(AdminUsersView)
    await wrapper.find('.s-page-header__actions button').trigger('click')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  })
})

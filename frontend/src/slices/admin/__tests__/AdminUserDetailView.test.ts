import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useConfirmDialog } from '@shared/composables'
import AdminUserDetailView from '../views/AdminUserDetailView.vue'

const route = {
  path: '/admin/users/:userId',
  name: 'admin.userDetail',
  component: AdminUserDetailView,
}

const LINKS = {
  set_password_url: 'https://smap.example/reset-password#token=fresh-set',
  verify_email_url: 'https://smap.example/verify-email#token=fresh-verify',
  set_password_expires_at: '2026-08-21T10:30:00Z',
  verify_email_expires_at: '2026-08-22T10:00:00Z',
}

function seedUser(overrides: Record<string, unknown> = {}): void {
  server.use(
    http.get('/api/admin/users/:userId', () =>
      HttpResponse.json({
        id: 'u_1',
        email: 'student@example.com',
        display_name: null,
        status: 'pending',
        email_verified: false,
        is_admin: false,
        banned_reason: null,
        banned_at: null,
        deleted_at: null,
        last_login_at: null,
        created_at: '2026-08-21T10:00:00Z',
        org_ids: [],
        project_ids: [],
        ...overrides,
      }),
    ),
  )
}

function reissueButton(wrapper: { findAll: (s: string) => Array<{ text: () => string }> }) {
  return wrapper
    .findAll('.admin-user-actions button')
    .find(b => b.text() === 'admin.userDetail.reissueLinks')
}

describe('AdminUserDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  // F-26: the whole template sat behind the pending flag, page header included,
  // so a cold load painted nothing but a 24px spinner row at the top left.
  it('paints the page header while the user is still loading', async () => {
    seedUser()
    // Deliberately no settle — the query is still in flight here.
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    expect(wrapper.find('.s-spinner').exists()).toBe(true)
    expect(wrapper.find('.s-page-header').exists()).toBe(true)
  })

  it('shows user email and action buttons when loaded', async () => {
    server.use(
      http.get('/api/admin/users/:userId', () =>
        HttpResponse.json({
          id: 'u_1',
          email: 'admin@example.com',
          status: 'active',
          email_verified: true,
          is_admin: true,
          banned_reason: null,
          banned_at: null,
          deleted_at: null,
          last_login_at: '2026-04-01T00:00:00Z',
          created_at: '2026-01-01T00:00:00Z',
          org_ids: ['org_1'],
          project_ids: ['proj_1'],
        }),
      ),
    )
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('admin@example.com')
    expect(wrapper.findAll('.admin-user-actions button').length).toBeGreaterThanOrEqual(1)
  })

  // The button is offered for every live account rather than gated on a
  // client-side guess at "still needs activation": the server's predicate
  // includes a linked identity, which the client cannot see, and a provisioned
  // user who walked the verification link first is verified but credential-less
  // and still needs the set-password link (D-16).
  it('offers re-issue for a verified account too, leaving the refusal to the server', async () => {
    seedUser({ status: 'active', email_verified: true })
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    await vi.waitFor(() => {
      if (!reissueButton(wrapper)) throw new Error('no re-issue action yet')
    })
  })

  // The server's guard reads a banned, password-less account as "not yet
  // activated" and mints for it (FU-9), so the client must not offer the action
  // for someone the operator deliberately locked out.
  it('hides re-issue for a banned account', async () => {
    seedUser({ status: 'banned', banned_reason: 'spam', banned_at: '2026-08-21T11:00:00Z' })
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    await vi.waitFor(() => {
      if (!wrapper.text().includes('student@example.com')) throw new Error('not loaded yet')
    })
    expect(reissueButton(wrapper)).toBeUndefined()
  })

  it('hides re-issue for a deleted account', async () => {
    seedUser({ status: 'deleted', deleted_at: '2026-08-21T11:00:00Z' })
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    await vi.waitFor(() => {
      if (!wrapper.text().includes('student@example.com')) throw new Error('not loaded yet')
    })
    expect(reissueButton(wrapper)).toBeUndefined()
  })

  it('re-mints both links and shows them', async () => {
    seedUser()
    server.use(
      http.post('/api/admin/users/:userId/activation-links', () =>
        HttpResponse.json(LINKS, { status: 201 }),
      ),
    )
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })

    await vi.waitFor(() => {
      if (!reissueButton(wrapper)) throw new Error('no re-issue action yet')
    })
    await (reissueButton(wrapper) as unknown as { trigger: (e: string) => Promise<void> })
      .trigger('click')

    // The mint is gated behind SConfirmDialog — it invalidates any earlier
    // unused link, so it must not fire on a stray click. The dialog component
    // is mounted by App.vue, which `renderView` does not include, so the
    // confirmation is driven through the composable's own singleton state.
    const { state, handleConfirm } = useConfirmDialog()
    await vi.waitFor(() => {
      if (!state.open) throw new Error('confirm dialog not opened')
    })
    handleConfirm()

    await vi.waitFor(() => {
      if (!wrapper.find('#adminSetPasswordUrl').exists()) throw new Error('no links dialog yet')
    })
    expect((wrapper.find('#adminSetPasswordUrl').element as HTMLInputElement).value)
      .toBe(LINKS.set_password_url)
    expect((wrapper.find('#adminVerifyEmailUrl').element as HTMLInputElement).value)
      .toBe(LINKS.verify_email_url)
  })
})

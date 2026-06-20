import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminUserDetailView from '../views/AdminUserDetailView.vue'

const route = {
  path: '/admin/users/:userId',
  name: 'admin.userDetail',
  component: AdminUserDetailView,
}

describe('AdminUserDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminUserDetailView, {
      routes: [route],
      initialRoute: '/admin/users/u_1',
    })
    expect(wrapper.exists()).toBe(true)
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
})

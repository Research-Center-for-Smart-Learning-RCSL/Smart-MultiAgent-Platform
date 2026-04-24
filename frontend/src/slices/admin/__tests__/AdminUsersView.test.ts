import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminUsersView from '../views/AdminUsersView.vue'

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
})

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminAuditView from '../views/AdminAuditView.vue'

describe('AdminAuditView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminAuditView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the filter form with multiple inputs', async () => {
    const wrapper = await renderView(AdminAuditView)
    const inputs = wrapper.findAll('form input')
    expect(inputs.length).toBeGreaterThanOrEqual(6)
    expect(wrapper.find('form button[type="submit"]').exists()).toBe(true)
  })

  it('renders audit log rows when data is loaded', async () => {
    server.use(
      http.get('/api/admin/audit', () =>
        HttpResponse.json({
          items: [
            { id: 'a_1', action: 'user.ban', actor_user_id: 'u_1', resource_type: 'user', resource_id: 'u_2', actor_ip: '10.0.0.1', created_at: '2026-04-01T00:00:00Z' },
          ],
          next_cursor: null,
        }),
      ),
    )
    const wrapper = await renderView(AdminAuditView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('user.ban')
  })
})

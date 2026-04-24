import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminOrgsView from '../views/AdminOrgsView.vue'

describe('AdminOrgsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminOrgsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders org rows with action buttons', async () => {
    server.use(
      http.get('/api/admin/orgs', () =>
        HttpResponse.json([
          { id: 'org_1', name: 'Acme', creator_user_id: 'u_1', created_at: '2026-01-01T00:00:00Z', deleted_at: null },
        ]),
      ),
    )
    const wrapper = await renderView(AdminOrgsView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('Acme')
    expect(wrapper.findAll('tbody button').length).toBeGreaterThanOrEqual(1)
  })
})

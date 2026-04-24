import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminProjectsView from '../views/AdminProjectsView.vue'

describe('AdminProjectsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminProjectsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders project rows as read-only (no action buttons)', async () => {
    server.use(
      http.get('/api/admin/projects', () =>
        HttpResponse.json([
          { id: 'proj_1', name: 'Alpha', owner_user_id: 'u_1', owner_org_id: null, created_at: '2026-01-01T00:00:00Z', deleted_at: null },
        ]),
      ),
    )
    const wrapper = await renderView(AdminProjectsView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('Alpha')
    expect(wrapper.findAll('tbody button').length).toBe(0)
  })
})

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminMetricsView from '../views/AdminMetricsView.vue'

describe('AdminMetricsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminMetricsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays metric cards with values when data loads', async () => {
    server.use(
      http.get('/api/admin/metrics', () =>
        HttpResponse.json({
          total_users: 100,
          total_orgs: 20,
          total_projects: 50,
          total_audit_entries: 5000,
        }),
      ),
    )
    const wrapper = await renderView(AdminMetricsView)
    await new Promise(r => setTimeout(r, 50))
    const cards = wrapper.findAll('.admin-metrics__card')
    expect(cards.length).toBe(4)
    expect(wrapper.text()).toContain('100')
    expect(wrapper.text()).toContain('5000')
  })
})

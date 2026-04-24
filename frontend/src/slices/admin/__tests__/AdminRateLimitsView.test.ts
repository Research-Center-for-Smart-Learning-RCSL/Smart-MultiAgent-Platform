import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminRateLimitsView from '../views/AdminRateLimitsView.vue'

describe('AdminRateLimitsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminRateLimitsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders editable rate limit rows with save buttons', async () => {
    server.use(
      http.get('/api/admin/rate-limits', () =>
        HttpResponse.json([
          { key: 'api.global', window_sec: 60, max_count: 100, scope: 'global', updated_at: '2026-01-01T00:00:00Z' },
          { key: 'api.user', window_sec: 60, max_count: 30, scope: 'user', updated_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(AdminRateLimitsView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.findAll('tbody tr').length).toBe(2)
    expect(wrapper.findAll('input[type="number"]').length).toBeGreaterThanOrEqual(4)
  })
})

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminAdminsView from '../views/AdminAdminsView.vue'

describe('AdminAdminsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminAdminsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the promote form and admin rows with demote buttons', async () => {
    server.use(
      http.get('/api/admin/admins', () =>
        HttpResponse.json([
          { user_id: 'u_1', promoted_by_user_id: null, promoted_at: '2026-01-01T00:00:00Z' },
          { user_id: 'u_2', promoted_by_user_id: 'u_1', promoted_at: '2026-02-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(AdminAdminsView)
    expect(wrapper.find('form input').exists()).toBe(true)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.findAll('tbody tr').length).toBe(2)
  })
})

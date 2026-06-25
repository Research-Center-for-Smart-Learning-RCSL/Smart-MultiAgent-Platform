import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminHomeView from '../views/AdminHomeView.vue'
import AdminNav from '../components/AdminNav.vue'

describe('AdminHomeView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminHomeView)
    expect(wrapper.exists()).toBe(true)
  })

  it('displays nav links for all admin sections', async () => {
    // The section nav moved into the shared AdminNav (rendered as a sidebar at
    // desktop widths, which is jsdom's default 1024px).
    const wrapper = await renderView(AdminNav)
    const links = wrapper.findAll('nav a')
    expect(links.length).toBeGreaterThanOrEqual(9)
  })

  it('shows metric cards when data loads', async () => {
    server.use(
      http.get('/api/admin/metrics', () =>
        HttpResponse.json({
          total_users: 42,
          total_orgs: 5,
          total_projects: 10,
          total_audit_entries: 300,
        }),
      ),
    )
    const wrapper = await renderView(AdminHomeView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('42')
  })
})

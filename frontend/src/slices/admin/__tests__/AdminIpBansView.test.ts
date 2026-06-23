import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminIpBansView from '../views/AdminIpBansView.vue'

describe('AdminIpBansView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(AdminIpBansView)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the create-ban form with CIDR and reason inputs', async () => {
    const wrapper = await renderView(AdminIpBansView)
    const form = wrapper.find('form')
    expect(form.exists()).toBe(true)
    expect(form.findAll('input').length).toBe(2)
    expect(form.find('button[type="submit"]').exists()).toBe(true)
  })

  it('renders ban rows when data is loaded', async () => {
    server.use(
      http.get('/api/admin/ip-bans', () =>
        HttpResponse.json([
          { id: 'ban_1', cidr: '192.168.0.0/16', reason: 'Abuse', created_by_user_id: 'u1', banned_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(AdminIpBansView)
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('192.168.0.0/16')
    expect(wrapper.text()).toContain('Abuse')
  })
})

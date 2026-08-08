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

  // AC-6: the activities view deep-links here pre-filtered. Before this, the view
  // ignored the query string entirely and such a link silently showed everything.
  it('hydrates its filters from the route query', async () => {
    let seen: URL | null = null
    server.use(
      http.get('/api/admin/audit', ({ request }) => {
        seen = new URL(request.url)
        return HttpResponse.json({ items: [], next_cursor: null })
      }),
    )

    const wrapper = await renderView(AdminAuditView, {
      initialRoute: '/admin/audit?resource_type=activity_type&resource_id=at_42',
    })
    await new Promise(r => setTimeout(r, 50))

    // The form reflects the link...
    const values = wrapper.findAll('form input').map(i => (i.element as HTMLInputElement).value)
    expect(values).toContain('activity_type')
    expect(values).toContain('at_42')

    // ...and the filter is actually applied to the request, not just displayed.
    expect(seen).not.toBeNull()
    expect(seen!.searchParams.get('resource_type')).toBe('activity_type')
    expect(seen!.searchParams.get('resource_id')).toBe('at_42')
  })

  it('ignores query params that are not filter fields', async () => {
    let seen: URL | null = null
    server.use(
      http.get('/api/admin/audit', ({ request }) => {
        seen = new URL(request.url)
        return HttpResponse.json({ items: [], next_cursor: null })
      }),
    )

    await renderView(AdminAuditView, {
      initialRoute: '/admin/audit?limit=9999&cursor=evil&not_a_filter=x',
    })
    await new Promise(r => setTimeout(r, 50))

    expect(seen).not.toBeNull()
    expect(seen!.searchParams.get('not_a_filter')).toBeNull()
    // `limit`/`cursor` are the query's own concern; a link must not drive them.
    expect(seen!.searchParams.get('limit')).not.toBe('9999')
  })
})

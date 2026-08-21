import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import SessionsView from '../views/SessionsView.vue'

const mockSessions = [
  { id: 's1', user_agent: 'Chrome/120', ip_inet: '1.2.3.4', last_used_at: '2026-01-02T00:00:00Z', created_at: '2025-12-01T00:00:00Z', expires_at: '2026-02-01T00:00:00Z' },
  { id: 's2', user_agent: 'Firefox/121', ip_inet: '5.6.7.8', last_used_at: '2026-01-01T00:00:00Z', created_at: '2025-12-02T00:00:00Z', expires_at: '2026-02-02T00:00:00Z' },
]

describe('SessionsView', () => {
  it('renders without errors', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json([])))
    const wrapper = await renderView(SessionsView)
    expect(wrapper.exists()).toBe(true)
  })

  // F-27 / Q-10: a skeleton may never be taller than the shortest settled state
  // its branch can produce. Three 80px rects with 12px margins is 264px, and
  // this branch settles either to an SEmptyState or to a single ~60px session
  // row, so the card used to collapse upward when the query landed.
  it('keeps the skeleton no taller than a single settled session row', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json([])))
    // Deliberately no flushPromises — the request is still in flight here.
    const wrapper = await renderView(SessionsView)

    const skeletons = wrapper.findAll('.s-skeleton')
    expect(skeletons).toHaveLength(1)
    expect((skeletons[0]!.element as HTMLElement).style.height).toBe('56px')
  })

  it('lists sessions with revoke buttons', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json(mockSessions)))
    const wrapper = await renderView(SessionsView)
    await flushPromises()
    const items = wrapper.findAll('li')
    expect(items.length).toBe(2)
  })
})

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

  it('lists sessions with revoke buttons', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json(mockSessions)))
    const wrapper = await renderView(SessionsView)
    await flushPromises()
    const items = wrapper.findAll('li')
    expect(items.length).toBe(2)
  })
})

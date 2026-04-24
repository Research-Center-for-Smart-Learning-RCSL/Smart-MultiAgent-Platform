import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import SessionsView from '../views/SessionsView.vue'

const mockSessions = [
  { id: 's1', user_agent: 'Chrome', ip_inet: '1.2.3.4', last_used_at: '2026-01-01', is_current: true },
  { id: 's2', user_agent: 'Firefox', ip_inet: '5.6.7.8', last_used_at: '2026-01-02', is_current: false },
]

describe('SessionsView', () => {
  it('renders without errors', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json({ data: [] })))
    const wrapper = await renderView(SessionsView)
    expect(wrapper.exists()).toBe(true)
  })

  it('lists sessions and shows revoke button for non-current', async () => {
    server.use(http.get('/api/auth/sessions', () => HttpResponse.json({ data: mockSessions })))
    const wrapper = await renderView(SessionsView)
    await flushPromises()
    const items = wrapper.findAll('li')
    expect(items.length).toBe(2)
    expect(items[0].find('button').exists()).toBe(false)
    expect(items[1].find('button').exists()).toBe(true)
  })
})

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import NotificationsView from '../views/NotificationsView.vue'

const routes = [
  {
    path: '/notifications',
    name: 'notifications.list',
    component: NotificationsView,
  },
]

const NOTIF = {
  id: 'n_1',
  kind: 'invite.received',
  title: 'You were invited to Acme',
  body: 'Owner invited you to the Acme organization.',
  metadata: {},
  read_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

function seed(items: unknown[]): void {
  server.use(http.get('/api/notifications', () => HttpResponse.json(items)))
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

describe('NotificationsView', () => {
  it('lists notifications fetched from the backend', async () => {
    seed([NOTIF])
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('You were invited to Acme')
  })

  it('renders the empty state with no notifications', async () => {
    seed([])
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)
    expect(wrapper.find('.notifications__list').exists()).toBe(false)
  })
})

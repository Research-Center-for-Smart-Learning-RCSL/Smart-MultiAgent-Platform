import { describe, it, expect, beforeAll, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { installNotificationsSlice } from '..'
import NotificationsView from '../views/NotificationsView.vue'

const routes = [
  {
    path: '/notifications',
    name: 'notifications.list',
    component: NotificationsView,
  },
]

const INVITE = {
  id: 'n_1',
  kind: 'invite.received',
  title: 'You were invited to Acme',
  body: 'Owner invited you to the Acme organization.',
  metadata: { invite_id: 'inv_1', scope: 'org', scope_id: 'org_1' },
  read_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const KEY_FAILED = {
  id: 'n_2',
  kind: 'key.test_failed',
  title: 'API key test failed',
  body: 'Validation returned HTTP 401.',
  metadata: { key_id: 'key_99', provider: 'openai' },
  read_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const READ_ITEM = { ...INVITE, id: 'n_3', read_at: '2026-01-02T00:00:00Z' }

function seed(items: unknown[]): void {
  server.use(http.get('/api/notifications', () => HttpResponse.json(items)))
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

beforeAll(() => {
  // Register the slice i18n bundle so $t() resolves real strings in assertions.
  installNotificationsSlice()
})

describe('NotificationsView', () => {
  it('lists notifications fetched from the backend', async () => {
    seed([INVITE])
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
    expect(wrapper.find('.s-empty-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('You have no notifications.')
  })

  it('shows an error state (not the empty state) when the list fetch fails', async () => {
    server.use(
      http.get('/api/notifications', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    )
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)
    expect(wrapper.text()).toContain('Could not load notifications.')
    expect(wrapper.find('.s-empty-state').exists()).toBe(false)
  })

  it('renders a kind-specific action link from notification metadata', async () => {
    seed([KEY_FAILED])
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)
    const link = wrapper.find('.ncard__link')
    expect(link.exists()).toBe(true)
    expect(link.text()).toBe('View key')
  })

  it('shows a mark-read control only for unread notifications', async () => {
    seed([READ_ITEM])
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)
    expect(wrapper.find('.ncard').exists()).toBe(true)
    expect(wrapper.find('.ncard__mark').exists()).toBe(false)
  })

  it('marks a notification read and removes its mark-read control', async () => {
    seed([INVITE])
    const markRead = vi.fn()
    server.use(
      http.post('/api/notifications/read', async ({ request }) => {
        markRead(await request.json())
        return HttpResponse.json({ marked: 1 })
      }),
    )
    const wrapper = await renderView(NotificationsView, {
      routes,
      initialRoute: '/notifications',
    })
    await settle(wrapper)

    await wrapper.find('.ncard__mark').trigger('click')
    await settle(wrapper)

    expect(markRead).toHaveBeenCalledWith({ ids: ['n_1'] })
    expect(wrapper.find('.ncard__mark').exists()).toBe(false)
  })
})

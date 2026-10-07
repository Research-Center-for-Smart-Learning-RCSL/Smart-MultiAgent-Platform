// Banned guests and link rotation in room settings
// (docs/tasks/2026-10-05-guest-kick-and-ban, AC-4, AC-5).

import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useConfirmDialog } from '@shared/composables'
import ChatroomSettingsView from '../views/ChatroomSettingsView.vue'
import type { Chatroom } from '../types'

const routes = [
  { path: '/chatrooms/:chatroomId/settings', name: 'conversation.chatroom.settings', component: ChatroomSettingsView },
  { path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: { template: '<div />' } },
  { path: '/workspaces/:workspaceId/chatrooms', name: 'conversation.chatrooms', component: { template: '<div />' } },
]

function room(isModerator: boolean): Chatroom {
  return {
    id: 'cr_1',
    workspace_id: 'ws_1',
    name: 'Room One',
    allow_org_members: false,
    allow_project_members: true,
    allow_project_owners_only: false,
    allow_guest_links: true,
    version: 1,
    created_at: new Date().toISOString(),
    is_moderator: isModerator,
  } as Chatroom
}

async function mount(isModerator: boolean) {
  const r = room(isModerator)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  qc.setQueryData(['conversation', 'chatrooms', r.workspace_id], [r])
  server.use(
    http.get('/api/chatrooms/:id', () => HttpResponse.json(r)),
    http.get('/api/chatrooms/:id/guest-link', () =>
      HttpResponse.json({ url: 'https://smap.test/g/cr_1/old-token', chatroom_id: 'cr_1', guest_token: 'old-token' }),
    ),
  )
  const wrapper = await renderView(ChatroomSettingsView, {
    routes,
    initialRoute: '/chatrooms/cr_1/settings',
    queryClient: qc,
  })
  for (let i = 0; i < 4; i++) await flushPromises()
  return wrapper
}

describe('ChatroomSettingsView guest moderation', () => {
  it('lists bans and lifts one, re-reading the list', async () => {
    let bans = [{ id: 'b_1', display_name: 'Carol', created_at: '2026-10-06T08:00:00Z' }]
    let lifted: string | null = null
    server.use(
      http.get('/api/chatrooms/:id/guest-bans', () => HttpResponse.json(bans)),
      http.delete('/api/chatrooms/:id/guest-bans/:banId', ({ params }) => {
        lifted = String(params.banId)
        bans = []
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const wrapper = await mount(true)

    const rows = wrapper.findAll('[data-testid="banned-guest"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('Carol')

    await rows[0]!.find('button').trigger('click')
    for (let i = 0; i < 4; i++) await flushPromises()

    expect(lifted).toBe('b_1')
    expect(wrapper.find('[data-testid="banned-guest"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="no-banned-guests"]').exists()).toBe(true)
  })

  it('rotates the link only after the confirm, and shows the new one', async () => {
    let rotations = 0
    server.use(
      http.get('/api/chatrooms/:id/guest-bans', () => HttpResponse.json([])),
      http.post('/api/chatrooms/:id/guest-link/rotate', () => {
        rotations += 1
        return HttpResponse.json({ url: 'https://smap.test/g/cr_1/new-token', chatroom_id: 'cr_1', guest_token: 'new-token' })
      }),
    )
    const wrapper = await mount(true)
    const link = () => (wrapper.find('.guest-link input').element as HTMLInputElement).value
    expect(link()).toContain('old-token')

    await wrapper.find('[data-testid="rotate-guest-link"]').trigger('click')
    await flushPromises()
    expect(rotations).toBe(0)
    useConfirmDialog().handleConfirm()
    for (let i = 0; i < 4; i++) await flushPromises()

    expect(rotations).toBe(1)
    expect(link()).toContain('new-token')
  })

  it('shows neither the ban list nor rotation to a non-moderator, and asks for no bans', async () => {
    let banReads = 0
    server.use(
      http.get('/api/chatrooms/:id/guest-bans', () => {
        banReads += 1
        return HttpResponse.json([])
      }),
    )
    const wrapper = await mount(false)

    expect(wrapper.find('[data-testid="rotate-guest-link"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('conversation.settings.bannedGuests')
    expect(banReads).toBe(0)
  })
})

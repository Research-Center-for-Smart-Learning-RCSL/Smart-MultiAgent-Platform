// Removing and banning an anonymous guest (docs/tasks/2026-10-05-guest-kick-and-ban,
// AC-1, AC-3, AC-6): the removed guest's room ends on the room event, the 4408
// close or the problem type, and moderators get the actions on anonymous guests only.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { clearGuestContext, guestSessionEnd, setAccessToken, setGuestContext } from '@shared/transport'
import { markConnectionRestored } from '@shared/composables/useNetworkStatus'
import { useConfirmDialog } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
import ChatroomView from '../views/ChatroomView.vue'
import { useConversationStore } from '../stores/conversation'
import { FakeWebSocket, composerDisabled, settle, unsignedToken } from './kit'

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]
const GUEST = 'g_1'
const removedKey = 'conversation.guest.removed'

function enterAsGuest(): void {
  setAccessToken(unsignedToken({ sub: GUEST, token_use: 'guest_access', display_name: 'Alice', chatroom_id: 'cr_1' }))
  setGuestContext('cr_1')
}

let width = 0
beforeEach(() => {
  width = window.innerWidth
  window.innerWidth = 1440
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  server.use(http.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })))
})

afterEach(() => {
  vi.unstubAllGlobals()
  setAccessToken(null)
  clearGuestContext()
  markConnectionRestored()
  window.innerWidth = width
})

describe('ChatroomView for a removed guest', () => {
  it('shows the removed banner at once on the room event naming this session, with no Rejoin', async () => {
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    const before = FakeWebSocket.instances.length

    socket.frame({ type: 'chatroom.guest_removed', chatroom_id: 'cr_1', guest_session_id: GUEST })
    await settle()
    await new Promise((r) => setTimeout(r, 1200))

    expect(wrapper.text()).toContain(removedKey)
    expect(wrapper.text()).not.toContain('conversation.guest.rejoin')
    expect(composerDisabled(wrapper)).toBe(true)
    expect(socket.closedWith).toBe(1000)
    expect(FakeWebSocket.instances).toHaveLength(before)
  })

  it('ignores a removal frame naming another session', async () => {
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()

    socket.frame({ type: 'chatroom.guest_removed', chatroom_id: 'cr_1', guest_session_id: 'g_other' })
    await settle()

    expect(wrapper.text()).not.toContain(removedKey)
    expect(composerDisabled(wrapper)).toBe(false)
  })

  it('a 4408 close shows the removed banner and stops reconnecting', async () => {
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    const before = FakeWebSocket.instances.length

    socket.serverClose(4408)
    await settle()
    await new Promise((r) => setTimeout(r, 1200))
    await settle()

    expect(wrapper.text()).toContain(removedKey)
    expect(FakeWebSocket.instances).toHaveLength(before)
    expect(composerDisabled(wrapper)).toBe(true)
  })

  it('a guest-removed answer to a room request records the removal', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/conversation/guest-removed', title: 't', status: 403 },
          { status: 403 },
        ),
      ),
    )
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(guestSessionEnd.value).toBe('removed')
    expect(wrapper.text()).toContain(removedKey)
  })
})

describe('ChatroomView moderator actions', () => {
  const guestMessage = {
    id: 'm_1',
    chatroom_id: 'cr_1',
    sender_type: 'guest',
    sender_id: 'g_2',
    content_md: 'hi',
    metadata: {},
    version: 1,
    created_at: new Date().toISOString(),
    edited_at: null,
    deleted_at: null,
  }

  function room(isModerator: boolean) {
    return {
      id: 'cr_1',
      name: 'Room',
      workspace_id: 'ws_1',
      allow_org_members: false,
      allow_project_members: true,
      allow_project_owners_only: false,
      allow_guest_links: true,
      agents: [],
      is_moderator: isModerator,
    }
  }

  function serveRoom(isModerator: boolean): void {
    useSessionStore().me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
    server.use(
      http.get('/api/chatrooms/cr_1', () => HttpResponse.json(room(isModerator))),
      http.get('/api/chatrooms/cr_1/members', () =>
        HttpResponse.json([
          { user_id: 'g_2', display_name: 'Carol', kind: 'guest_session' },
          { user_id: 'u_8', display_name: 'Olive', kind: 'room_guest' },
          { user_id: 'u_7', display_name: 'Ms Lin', kind: 'member' },
        ]),
      ),
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([guestMessage, { ...guestMessage, id: 'm_2', sender_type: 'user', sender_id: 'u_8' }]),
      ),
    )
  }

  it('offers remove and ban on anonymous guest sessions only, never on a registered guest', async () => {
    serveRoom(true)
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', ['u_1', 'g_2', 'u_8', 'u_7'])
    await settle()

    for (const presence of wrapper.findAllComponents(ChatroomPresence)) {
      const rows = presence.props('onlineUsers') as Array<{ id: string; removable: boolean }>
      expect(Object.fromEntries(rows.map((r) => [r.id, r.removable]))).toEqual({
        u_1: false,
        g_2: true,
        u_8: false,
        u_7: false,
      })
    }
    const bubbles = wrapper.findAllComponents(ChatroomMessageBubble)
    const byId = Object.fromEntries(bubbles.map((b) => [b.props('message').id, b.props('canModerateGuest')]))
    expect(byId).toEqual({ m_1: true, m_2: false })
  })

  it('offers nothing to a viewer who is not a moderator', async () => {
    serveRoom(false)
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', ['g_2'])
    await settle()

    for (const presence of wrapper.findAllComponents(ChatroomPresence)) {
      for (const row of presence.props('onlineUsers') as Array<{ removable: boolean }>) {
        expect(row.removable).toBe(false)
      }
    }
    expect(wrapper.find('[data-testid="bubble-remove-guest"]').exists()).toBe(false)
  })

  it('bans after the confirm, naming the session in the request', async () => {
    serveRoom(true)
    let sent: { url: string; body: unknown } | null = null
    server.use(
      http.post('/api/chatrooms/cr_1/guests/:sid/remove', async ({ request, params }) => {
        sent = { url: String(params.sid), body: await request.json() }
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    await wrapper.find('#msg-m_1 [data-testid="bubble-ban-guest"]').trigger('click')
    await settle()
    expect(sent).toBeNull()
    useConfirmDialog().handleConfirm()
    await settle()

    expect(sent).toEqual({ url: 'g_2', body: { ban: true } })
  })
})

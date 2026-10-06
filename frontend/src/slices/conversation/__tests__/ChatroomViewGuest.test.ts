// An anonymous guest in the room (docs/tasks/2026-10-05-guest-room-read-and-identity).
//
// A guest holds a guest token and no `session.me`. Everything here failed because
// the view identified "me" by `session.me` and gated the header on a room field an
// anonymous guest never received: the guest saw settings, export and Back, could
// not find its own row to rename, saw its own typing indicator, and saw every
// other guest as an eight-character id.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import {
  clearGuestContext,
  getAccessToken,
  guestSessionId,
  markGuestSessionEnded,
  setAccessToken,
  setGuestContext,
} from '@shared/transport'
import { markConnectionRestored } from '@shared/composables/useNetworkStatus'
import ChatroomComposer from '../components/ChatroomComposer.vue'
import { useGuestSessionStore } from '../stores/guestSession'
import { useSessionStore } from '@shared/stores/session'
import ChatroomView from '../views/ChatroomView.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import ChatroomTypingIndicator from '../components/ChatroomTypingIndicator.vue'
import { useConversationStore } from '../stores/conversation'

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]
const GUEST = 'g_1'
const settingsKey = 'conversation.chatroom.settingsLabel'
const backKey = 'conversation.chatroom.back'

function unsignedToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.s`
}

function enterAsGuest(): void {
  setAccessToken(
    unsignedToken({ sub: GUEST, token_use: 'guest_access', display_name: 'Alice', chatroom_id: 'cr_1' }),
  )
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await nextTick()
}

let width = 0
beforeEach(() => {
  width = window.innerWidth
  // The full three-column layout: a standing people rail, not a drawer.
  window.innerWidth = 1440
})

afterEach(() => {
  setAccessToken(null)
  window.innerWidth = width
})

describe('guestSessionId', () => {
  it('is the guest token subject, and nothing for a user token', () => {
    enterAsGuest()
    expect(guestSessionId.value).toBe(GUEST)
    setAccessToken(unsignedToken({ sub: 'u_1', token_use: 'access' }))
    expect(guestSessionId.value).toBeNull()
  })
})

describe('ChatroomView for an anonymous guest', () => {
  it('shows no settings, export or Back even when the room read fails', async () => {
    enterAsGuest()
    server.use(http.get('/api/chatrooms/cr_1', () => HttpResponse.json({ status: 403 }, { status: 403 })))
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(wrapper.find(`[aria-label="${settingsKey}"]`).exists()).toBe(false)
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(false)
    expect(wrapper.find(`[aria-label="${backKey}"]`).exists()).toBe(false)
  })

  it('marks its own row in the people rail and offers the rename control', async () => {
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', [GUEST, 'u_2'])
    await settle()

    const rail = wrapper.findAllComponents(ChatroomPresence)
    expect(rail.length).toBeGreaterThan(0)
    for (const presence of rail) {
      expect(presence.props('viewerIsGuest')).toBe(true)
      const own = presence.props('onlineUsers').find((u: { id: string }) => u.id === GUEST)
      expect(own?.isYou).toBe(true)
    }
  })

  it('does not show its own typing indicator, but does show others', async () => {
    enterAsGuest()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    const store = useConversationStore()
    store.addTyping('cr_1', GUEST)
    store.addTyping('cr_1', 'u_2abcdefgh')
    await settle()

    // The server echoes typing frames to the sender too (ws/chatroom.py), so the
    // filter is the only thing keeping the guest's own entry out.
    const typers = wrapper.findComponent(ChatroomTypingIndicator).props('typers') as Array<{ name: string }>
    expect(typers).toHaveLength(1)
    expect(typers[0].name).not.toContain(GUEST)
  })

  it("labels another guest's messages with the name from the roster", async () => {
    enterAsGuest()
    server.use(
      http.get('/api/chatrooms/cr_1/members', () =>
        HttpResponse.json([{ user_id: 'g_2', display_name: 'Carol', kind: 'guest_session' }]),
      ),
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'guest',
            sender_id: 'g_2',
            content_md: 'hello',
            metadata: {},
            version: 1,
            created_at: new Date().toISOString(),
            edited_at: null,
            deleted_at: null,
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(wrapper.text()).toContain('Carol')
  })

  it('names the room agents from the room agent list, which a guest can read', async () => {
    enterAsGuest()
    server.use(
      http.get('/api/projects/:projectId/agents', () => HttpResponse.json({ status: 403 }, { status: 403 })),
      http.get('/api/chatrooms/cr_1/agents', () => HttpResponse.json([{ agent_id: 'a_1', name: 'Tutor' }])),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    const agents = wrapper.findComponent(ChatroomPresence).props('agents') as Array<{ name: string }>
    expect(agents.map((a) => a.name)).toEqual(['Tutor'])
  })

  it('shows the stored name after a rename and re-reads the roster', async () => {
    enterAsGuest()
    let rosterReads = 0
    let sent: unknown = null
    server.use(
      http.get('/api/chatrooms/cr_1/members', () => {
        rosterReads += 1
        return HttpResponse.json([])
      }),
      http.put('/api/guest/session/:sid/display-name', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ display_name: 'Bob' })
      }),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', [GUEST])
    await settle()
    const before = rosterReads

    wrapper.findComponent(ChatroomPresence).vm.$emit('update-display-name', 'Bob  ')
    await settle()

    expect(sent).toEqual({ display_name: 'Bob  ' })
    expect(wrapper.findComponent(ChatroomPresence).props('viewerName')).toBe('Bob')
    expect(rosterReads).toBeGreaterThan(before)
  })
})

// docs/tasks/2026-10-05-guest-frontend-session-lifecycle (F-7, F-8, F-20, Q-2)
describe('ChatroomView when the guest session ends', () => {
  class FakeWebSocket {
    static readonly CONNECTING = 0
    static readonly OPEN = 1
    static readonly CLOSING = 2
    static readonly CLOSED = 3
    static instances: FakeWebSocket[] = []
    readyState = FakeWebSocket.CONNECTING
    closedWith: number | null = null
    onopen: (() => void) | null = null
    onclose: ((ev: { code: number; reason: string }) => void) | null = null
    onmessage: ((ev: { data: string }) => void) | null = null
    onerror: (() => void) | null = null
    constructor() {
      FakeWebSocket.instances.push(this)
    }
    send(): void {}
    close(code?: number): void {
      this.closedWith = code ?? 1000
      this.readyState = FakeWebSocket.CLOSED
    }
    open(): void {
      this.readyState = FakeWebSocket.OPEN
      this.onopen?.()
    }
    serverClose(code: number): void {
      this.readyState = FakeWebSocket.CLOSED
      this.onclose?.({ code, reason: '' })
    }
  }

  const expiredKey = 'conversation.guest.sessionExpired'
  const reopenKey = 'conversation.guest.sessionExpiredReopen'
  const disabledKey = 'conversation.guest.guestDisabled'
  const rejoinKey = 'conversation.guest.rejoin'

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    server.use(http.post('/api/guest/ws-ticket', () => HttpResponse.json({ ticket: 't', expires_in: 30 })))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    clearGuestContext()
    markConnectionRestored()
  })

  function composerDisabled(wrapper: Awaited<ReturnType<typeof renderView>>): boolean {
    return wrapper.findComponent(ChatroomComposer).props('disabled') === true
  }

  // 4401 is the server's "re-handshake" signal (ws_auth.py), so the refresh it
  // triggers decides whether the session ended (code review finding 1).
  it('shows the expired banner when the refresh a 4401 close triggers is answered 404', async () => {
    server.use(
      http.post('/api/guest/cr_1/refresh', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/conversation/guest-token-invalid', title: 't', status: 404 },
          { status: 404 },
        ),
      ),
    )
    enterAsGuest()
    setGuestContext('cr_1')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useGuestSessionStore().setGuestToken('cr_1', 'tok_abcdefghijklmnop')
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()

    setAccessToken(null)
    socket.serverClose(4401)
    await settle()

    expect(wrapper.text()).toContain(expiredKey)
    expect(wrapper.text()).toContain(rejoinKey)
    expect(composerDisabled(wrapper)).toBe(true)
  })

  it('a 4401 close whose refresh succeeds keeps the session and reconnects', async () => {
    let refreshes = 0
    server.use(
      http.post('/api/guest/cr_1/refresh', () => {
        refreshes += 1
        return HttpResponse.json({
          access_token: unsignedToken({ sub: GUEST, token_use: 'guest_access', chatroom_id: 'cr_1' }),
        })
      }),
    )
    enterAsGuest()
    setGuestContext('cr_1')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    const before = FakeWebSocket.instances.length

    socket.serverClose(4401)
    await settle()
    await new Promise((r) => setTimeout(r, 1200))
    await settle()

    expect(refreshes).toBe(1)
    expect(wrapper.text()).not.toContain(expiredKey)
    expect(composerDisabled(wrapper)).toBe(false)
    expect(FakeWebSocket.instances.length).toBeGreaterThan(before)
  })

  it('shows the disabled banner whichever path recorded it, and stops the socket', async () => {
    enterAsGuest()
    setGuestContext('cr_1')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    const before = FakeWebSocket.instances.length

    markGuestSessionEnded('disabled')
    await settle()
    await new Promise((r) => setTimeout(r, 1200))

    expect(wrapper.text()).toContain(disabledKey)
    expect(socket.closedWith).toBe(1000)
    expect(FakeWebSocket.instances).toHaveLength(before)
    expect(composerDisabled(wrapper)).toBe(true)
  })

  // docs/tasks/2026-10-05-guest-session-backend-hardening (AC-3, Q-3): a room
  // whose workspace or project was deleted closes 4404 for every viewer.
  const goneKey = 'conversation.chatroom.roomGone'

  it.each([
    ['a guest', () => { enterAsGuest(); setGuestContext('cr_1') }],
    ['a member', () => {
      useSessionStore().me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
    }],
  ])('a 4404 close shows %s that the room is gone and stops reconnecting', async (_who, enter) => {
    enter()
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    const socket = FakeWebSocket.instances.at(-1)!
    socket.open()
    const before = FakeWebSocket.instances.length

    socket.serverClose(4404)
    await settle()
    await new Promise((r) => setTimeout(r, 1200))
    await settle()

    expect(wrapper.text()).toContain(goneKey)
    expect(wrapper.text()).not.toContain(expiredKey)
    expect(FakeWebSocket.instances).toHaveLength(before)
    expect(composerDisabled(wrapper)).toBe(true)
  })

  // Lifecycle FU-9: a guest whose socket dropped for another reason reconnects
  // through the ticket, which is where a deleted room answers.
  it('shows a guest that the room is gone when the reconnect ticket says so', async () => {
    let tickets = 0
    server.use(
      http.post('/api/guest/ws-ticket', () => {
        tickets += 1
        return HttpResponse.json(
          { type: 'https://smap.local/problems/conversation/chatroom-not-found', title: 't', status: 404 },
          { status: 404 },
        )
      }),
    )
    enterAsGuest()
    setGuestContext('cr_1')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    await new Promise((r) => setTimeout(r, 1200))
    await settle()

    expect(wrapper.text()).toContain(goneKey)
    expect(tickets).toBe(1)
    expect(composerDisabled(wrapper)).toBe(true)
  })

  it('after a reload, tells the guest to reopen the shared link and offers sign-in, not Rejoin', async () => {
    setGuestContext('cr_1')
    markGuestSessionEnded('expired')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(wrapper.text()).toContain(reopenKey)
    expect(wrapper.text()).toContain('conversation.guest.signIn')
    expect(wrapper.text()).not.toContain(rejoinKey)
    expect(FakeWebSocket.instances).toHaveLength(0)
    // Still a guest's room: no member header.
    expect(wrapper.find(`[aria-label="${settingsKey}"]`).exists()).toBe(false)
  })

  it('a live socket does not clear a recorded end', async () => {
    enterAsGuest()
    setGuestContext('cr_1')
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    markGuestSessionEnded('expired')
    await settle()
    FakeWebSocket.instances.at(-1)!.open()
    await settle()

    expect(wrapper.text()).toContain(expiredKey)
  })

  it('an offline-restored guest resumes when the network returns and re-reads the room', async () => {
    setGuestContext('cr_1')
    let refreshOk = false
    let roomReads = 0
    server.use(
      http.post('/api/guest/cr_1/refresh', () =>
        refreshOk ? HttpResponse.json({ access_token: unsignedToken({ sub: GUEST, token_use: 'guest_access', chatroom_id: 'cr_1' }) }) : HttpResponse.error(),
      ),
      http.get('/api/chatrooms/cr_1', () => {
        roomReads += 1
        return HttpResponse.json({ id: 'cr_1', name: 'Room', workspace_id: 'ws_1', agents: [] })
      }),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    expect(wrapper.text()).not.toContain(expiredKey)
    expect(getAccessToken()).toBeNull()
    const readsBefore = roomReads

    refreshOk = true
    window.dispatchEvent(new Event('online'))
    await settle()

    expect(getAccessToken()).not.toBeNull()
    expect(roomReads).toBeGreaterThan(readsBefore)
    expect(FakeWebSocket.instances.length).toBeGreaterThan(0)
  })
})

// docs/tasks/2026-10-05-guest-sender-marking (AC-1, AC-2): every viewer sees a
// guest marked as one, decided by sender type or roster kind, never by the name.
describe('ChatroomView guest badges', () => {
  function message(id: string, senderType: string, senderId: string) {
    return {
      id,
      chatroom_id: 'cr_1',
      sender_type: senderType,
      sender_id: senderId,
      content_md: id,
      metadata: {},
      version: 1,
      created_at: new Date().toISOString(),
      edited_at: null,
      deleted_at: null,
    }
  }

  function bubbleBadged(wrapper: Awaited<ReturnType<typeof renderView>>, messageId: string): boolean {
    return wrapper.find(`#msg-${messageId} [data-testid="bubble-guest-badge"]`).exists()
  }

  it('badges an anonymous guest, a registered guest and no member, wherever they appear', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/members', () =>
        HttpResponse.json([
          { user_id: 'g_2', display_name: 'Ms Lin', kind: 'guest_session' },
          { user_id: 'u_8', display_name: 'Olive', kind: 'room_guest' },
          { user_id: 'u_7', display_name: 'Ms Lin', kind: 'member' },
        ]),
      ),
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          message('m_1', 'guest', 'g_2'),
          message('m_2', 'user', 'u_8'),
          message('m_3', 'user', 'u_7'),
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    const store = useConversationStore()
    store.setPresence('cr_1', ['g_2', 'u_8', 'u_7'])
    store.addTyping('cr_1', 'g_2')
    store.addTyping('cr_1', 'u_7')
    await settle()

    expect([bubbleBadged(wrapper, 'm_1'), bubbleBadged(wrapper, 'm_2'), bubbleBadged(wrapper, 'm_3')]).toEqual([
      true,
      true,
      false,
    ])
    for (const presence of wrapper.findAllComponents(ChatroomPresence)) {
      const rows = presence.props('onlineUsers') as Array<{ id: string; isGuest: boolean }>
      expect(Object.fromEntries(rows.map((r) => [r.id, r.isGuest]))).toEqual({ g_2: true, u_8: true, u_7: false })
    }
    const typers = wrapper.findComponent(ChatroomTypingIndicator).props('typers') as Array<{
      name: string
      isGuest: boolean
    }>
    expect(typers).toEqual([
      { name: 'Ms Lin', isGuest: true },
      { name: 'Ms Lin', isGuest: false },
    ])
  })

  it("badges a new guest's message before the roster knows them", async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/members', () => HttpResponse.json([])),
      http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([message('m_1', 'guest', 'g_9')])),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(bubbleBadged(wrapper, 'm_1')).toBe(true)
  })
})

describe('ChatroomView header for other viewers', () => {
  it('still shows settings, export and Back to a member', async () => {
    useSessionStore().me = {
      id: 'u_1',
      email: 'u@smap.test',
      email_verified: true,
      is_admin: false,
      status: 'active',
    }
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(wrapper.find(`[aria-label="${settingsKey}"]`).exists()).toBe(true)
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(true)
    expect(wrapper.find(`[aria-label="${backKey}"]`).exists()).toBe(true)
  })

  it('hides settings, export and Back from a registered guest, without offering a rename', async () => {
    useSessionStore().me = {
      id: 'u_9',
      email: 'g@smap.test',
      email_verified: true,
      is_admin: false,
      status: 'active',
    }
    server.use(
      http.get('/api/chatrooms/cr_1', () =>
        HttpResponse.json({
          id: 'cr_1',
          name: 'Test Room',
          project_id: 'proj_1',
          workspace_id: 'ws_1',
          allow_org_members: false,
          allow_project_members: true,
          allow_project_owners_only: false,
          allow_guest_links: false,
          agents: [],
          viewer_is_guest: true,
        }),
      ),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', ['u_9'])
    await settle()

    expect(wrapper.find(`[aria-label="${settingsKey}"]`).exists()).toBe(false)
    expect(wrapper.find('[data-testid="open-export"]').exists()).toBe(false)
    expect(wrapper.find(`[aria-label="${backKey}"]`).exists()).toBe(false)
    // The rename endpoint serves anonymous guest sessions only; a registered
    // guest's room label is chosen at enrolment and has no rename endpoint.
    for (const presence of wrapper.findAllComponents(ChatroomPresence)) {
      expect(presence.props('viewerIsGuest')).toBe(false)
    }
  })
})

// An anonymous guest in the room (docs/tasks/2026-10-05-guest-room-read-and-identity).
//
// A guest holds a guest token and no `session.me`. Everything here failed because
// the view identified "me" by `session.me` and gated the header on a room field an
// anonymous guest never received: the guest saw settings, export and Back, could
// not find its own row to rename, saw its own typing indicator, and saw every
// other guest as an eight-character id.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { guestSessionId, setAccessToken } from '@shared/transport'
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
    const names = wrapper.findComponent(ChatroomTypingIndicator).props('names') as string[]
    expect(names).toHaveLength(1)
    expect(names[0]).not.toContain(GUEST)
  })

  it("labels another guest's messages with the name from the roster", async () => {
    enterAsGuest()
    server.use(
      http.get('/api/chatrooms/cr_1/members', () => HttpResponse.json([{ user_id: 'g_2', display_name: 'Carol' }])),
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
    // guest's room label is set by the room's owner.
    for (const presence of wrapper.findAllComponents(ChatroomPresence)) {
      expect(presence.props('viewerIsGuest')).toBe(false)
    }
  })
})

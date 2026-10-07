// A present or typing participant the roster cannot name yet triggers one roster
// re-read (docs/tasks/2026-10-07-room-roster-completeness, AC-1, AC-2, AC-3, AC-5).
//
// Only an incoming message from an unknown sender used to re-read the roster, so a
// member who opened the room and read, or started typing a first message, stayed
// an eight-character id on every other screen.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
import ChatroomView from '../views/ChatroomView.vue'
import ChatroomComposer from '../components/ChatroomComposer.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import ChatroomTypingIndicator from '../components/ChatroomTypingIndicator.vue'
import { useConversationStore } from '../stores/conversation'
import { settle } from './kit'

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]
const ME = 'u_1aaaaaaaa'
const READER = 'u_2bbbbbbbb'
const TYPER = 'u_3cccccccc'
const NAMELESS = 'u_4dddddddd'

type Member = { user_id: string; display_name: string | null; kind: string }

let rosterReads = 0
let roster: Member[] = []

function serveRoster(): void {
  server.use(
    http.get('/api/chatrooms/cr_1/members', () => {
      rosterReads += 1
      return HttpResponse.json(roster)
    }),
  )
}

function onlineRow(wrapper: Awaited<ReturnType<typeof renderView>>, id: string) {
  const rows = wrapper.findComponent(ChatroomPresence).props('onlineUsers') as Array<{
    id: string
    displayName: string | null
  }>
  return rows.find((r) => r.id === id)
}

let width = 0
beforeEach(() => {
  rosterReads = 0
  roster = []
  width = window.innerWidth
  window.innerWidth = 1440
  serveRoster()
})

afterEach(() => {
  window.innerWidth = width
})

async function mount() {
  const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
  useSessionStore().me = { id: ME, email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
  await settle()
  return wrapper
}

describe('ChatroomView roster re-read for unknown participants', () => {
  it('names a present member who has not posted after one re-read', async () => {
    const wrapper = await mount()
    const before = rosterReads

    roster = [{ user_id: READER, display_name: 'Alice', kind: 'member' }]
    useConversationStore().setPresence('cr_1', [READER])
    await settle()

    expect(rosterReads).toBe(before + 1)
    expect(onlineRow(wrapper, READER)?.displayName).toBe('Alice')
    const mentionables = wrapper.findComponent(ChatroomComposer).props('agents') as Array<{
      id: string
      name: string
    }>
    expect(mentionables).toContainEqual({ id: READER, name: 'Alice' })
  })

  it("names the viewer's own row when the first roster read predates its join", async () => {
    const wrapper = await mount()

    roster = [{ user_id: ME, display_name: 'Me Myself', kind: 'member' }]
    useConversationStore().setPresence('cr_1', [ME])
    await settle()

    expect(onlineRow(wrapper, ME)?.displayName).toBe('Me Myself')
  })

  it('names a typing member from the first typing frame', async () => {
    const wrapper = await mount()
    const before = rosterReads

    roster = [{ user_id: TYPER, display_name: 'Bob', kind: 'member' }]
    useConversationStore().addTyping('cr_1', TYPER)
    await settle()

    expect(rosterReads).toBe(before + 1)
    const typers = wrapper.findComponent(ChatroomTypingIndicator).props('typers') as Array<{ name: string }>
    expect(typers.map((t) => t.name)).toEqual(['Bob'])
  })

  it('asks again after a failed roster read, on the next participant change', async () => {
    const wrapper = await mount()
    let failNext = true
    server.use(
      http.get('/api/chatrooms/cr_1/members', () => {
        rosterReads += 1
        if (failNext) {
          failNext = false
          return HttpResponse.json({ status: 502 }, { status: 502 })
        }
        return HttpResponse.json([{ user_id: READER, display_name: 'Alice', kind: 'member' }])
      }),
    )
    const store = useConversationStore()

    store.setPresence('cr_1', [READER])
    await settle()
    expect(onlineRow(wrapper, READER)?.displayName).toBeNull()

    store.addTyping('cr_1', READER)
    await settle()

    expect(onlineRow(wrapper, READER)?.displayName).toBe('Alice')
  })

  it('asks about an account with no display name once, wherever it appears', async () => {
    await mount()
    const before = rosterReads
    roster = [{ user_id: NAMELESS, display_name: null, kind: 'member' }]
    const store = useConversationStore()

    store.setPresence('cr_1', [NAMELESS])
    await settle()
    store.addTyping('cr_1', NAMELESS)
    await settle()
    store.removeTyping('cr_1', NAMELESS)
    store.addTyping('cr_1', NAMELESS)
    await settle()

    expect(rosterReads).toBe(before + 1)
  })
})

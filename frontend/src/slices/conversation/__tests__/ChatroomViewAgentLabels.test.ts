// Agent names on the room's history for every viewer
// (docs/tasks/2026-10-07-name-fallback-surfaces, AC-1, AC-2, AC-3).
//
// The view named agents from the room's current non-observer bindings, plus the
// project's live agents for members only. A guest saw an id on an unbound agent's
// messages and in a released observation's header, and every viewer saw an id once
// a deleted agent's binding was gone. The room's agent-label read fills that gap,
// and an agent nothing names is "Unknown agent", never its id.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient } from '@tanstack/vue-query'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { setAccessToken } from '@shared/transport'
import ChatroomView from '../views/ChatroomView.vue'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
import ChatroomSearchPanel from '../components/ChatroomSearchPanel.vue'
import ChatroomHeader from '../components/ChatroomHeader.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import { useConversationStore } from '../stores/conversation'
import { convKeys } from '../queries'
import type { Message } from '../types'
import { settle, unsignedToken } from './kit'

const routes = [{ path: '/chatrooms/:chatroomId', name: 'conversation.chatroom', component: ChatroomView }]

function message(id: string, over: Partial<Message>): Message {
  return {
    id,
    chatroom_id: 'cr_1',
    sender_type: 'agent',
    sender_id: null,
    content_md: id,
    metadata: {},
    version: 1,
    created_at: new Date().toISOString(),
    edited_at: null,
    deleted_at: null,
    ...over,
  }
}

const FROM_UNBOUND = message('m_unbound', { sender_id: 'a_unbound' })
const FROM_GONE = message('m_gone', { sender_id: 'a_gone' })
const RELEASED = message('m_released', {
  sender_type: 'system',
  metadata: { type: 'released_observation', observer_agent_id: 'a_observer' },
})

let labelReads = 0
let labels: Array<{ agent_id: string; name: string }> = []

function bubbleFor(wrapper: Awaited<ReturnType<typeof renderView>>, id: string) {
  const bubble = wrapper
    .findAllComponents(ChatroomMessageBubble)
    .find((b) => (b.props('message') as Message).id === id)
  if (!bubble) throw new Error(`no bubble for ${id}`)
  return bubble
}

beforeEach(() => {
  labelReads = 0
  labels = []
  setAccessToken(unsignedToken({ sub: 'g_1', token_use: 'guest_access', display_name: 'Alice', chatroom_id: 'cr_1' }))
  server.use(
    http.get('/api/chatrooms/cr_1/agent-labels', () => {
      labelReads += 1
      return HttpResponse.json(labels)
    }),
  )
})

afterEach(() => {
  setAccessToken(null)
})

describe('ChatroomView agent labels', () => {
  it('names an unbound author and a disclosed observer for a guest, and an unnamed agent as unknown', async () => {
    labels = [
      { agent_id: 'a_unbound', name: 'Tutor' },
      { agent_id: 'a_observer', name: 'Analyst' },
    ]
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([FROM_UNBOUND, RELEASED, FROM_GONE])),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(bubbleFor(wrapper, 'm_unbound').props('senderName')).toBe('Tutor')
    expect(bubbleFor(wrapper, 'm_released').props('agentNames')).toMatchObject({ a_observer: 'Analyst' })
    expect(bubbleFor(wrapper, 'm_gone').props('senderName')).toBe('conversation.chatroom.unknownAgent')
  })

  it('reads the labels once on open, even when one agent has no name', async () => {
    server.use(http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([FROM_GONE])))
    await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(labelReads).toBe(1)
  })

  it('re-reads the labels once when a live message names an agent nothing names yet', async () => {
    server.use(http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([])))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
      queryClient: qc,
    })
    await settle()
    const before = labelReads

    labels = [{ agent_id: 'a_observer', name: 'Analyst' }]
    qc.setQueryData<Message[]>(convKeys.messages('cr_1'), (prev) => [...(prev ?? []), RELEASED])
    await settle()
    qc.setQueryData<Message[]>(convKeys.messages('cr_1'), (prev) => [...(prev ?? []), FROM_GONE])
    await settle()
    qc.setQueryData<Message[]>(convKeys.messages('cr_1'), (prev) => [
      ...(prev ?? []),
      message('m_gone_2', { sender_id: 'a_gone' }),
    ])
    await settle()

    // One read for the observer, one for the unnamed agent; none for its second message.
    expect(labelReads).toBe(before + 2)
    expect(bubbleFor(wrapper, 'm_released').props('agentNames')).toMatchObject({ a_observer: 'Analyst' })
  })

  it('labels search hits by the same rule as messages, own guest rename included', async () => {
    labels = [{ agent_id: 'a_unbound', name: 'Tutor' }]
    server.use(
      http.get('/api/chatrooms/cr_1/members', () =>
        HttpResponse.json([
          { user_id: 'u_2', display_name: 'Teacher', kind: 'member' },
          { user_id: 'g_1', display_name: 'Alice', kind: 'guest_session' },
        ]),
      ),
      http.put('/api/guest/session/:sid/display-name', () => HttpResponse.json({ display_name: 'Bob' })),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    useConversationStore().setPresence('cr_1', ['g_1'])
    await settle()
    wrapper.findComponent(ChatroomHeader).vm.$emit('search')
    await settle()

    const label = () =>
      wrapper.findComponent(ChatroomSearchPanel).props('senderLabel') as (type: string, id: string | null) => string
    expect(label()('agent', 'a_unbound')).toBe('Tutor')
    expect(label()('agent', 'a_gone')).toBe('conversation.chatroom.unknownAgent')
    expect(label()('user', 'u_2')).toBe('Teacher')

    // A rename the roster has not caught up with: the own session's hits follow it,
    // as its messages do (code review finding 4).
    wrapper.findComponent(ChatroomPresence).vm.$emit('update-display-name', 'Bob')
    await settle()
    expect(label()('guest', 'g_1')).toBe('Bob')
  })

  // Code review finding 1: a read in flight when a release arrives does not cover
  // that release, so the observer is asked about again once the read lands.
  it('asks again about an observer whose release arrived while a read was in flight', async () => {
    let open!: () => void
    const gate = new Promise<void>((resolve) => {
      open = resolve
    })
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([])),
      http.get('/api/chatrooms/cr_1/agent-labels', async () => {
        labelReads += 1
        const answer = [...labels]
        if (labelReads === 1) await gate
        return HttpResponse.json(answer)
      }),
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1', queryClient: qc })
    await settle()
    expect(labelReads).toBe(1)

    labels = [{ agent_id: 'a_observer', name: 'Analyst' }]
    qc.setQueryData<Message[]>(convKeys.messages('cr_1'), (prev) => [...(prev ?? []), RELEASED])
    await settle()
    open()
    await settle()

    expect(labelReads).toBe(2)
    expect(bubbleFor(wrapper, 'm_released').props('agentNames')).toMatchObject({ a_observer: 'Analyst' })
  })

  // Code review findings 2 and 3: "Unknown agent" is stated only once the labels
  // have answered, not while they load and not after a failed read.
  it('shows the short id, not "Unknown agent", while the labels load', async () => {
    let open!: () => void
    const gate = new Promise<void>((resolve) => {
      open = resolve
    })
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([FROM_GONE, RELEASED])),
      http.get('/api/chatrooms/cr_1/agent-labels', async () => {
        await gate
        return HttpResponse.json([])
      }),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(bubbleFor(wrapper, 'm_gone').props('senderName')).toBe('a_gone'.slice(0, 8))
    expect(bubbleFor(wrapper, 'm_released').props('agentNamesSettled')).toBe(false)

    open()
    await settle()
    expect(bubbleFor(wrapper, 'm_gone').props('senderName')).toBe('conversation.chatroom.unknownAgent')
    expect(bubbleFor(wrapper, 'm_released').props('agentNamesSettled')).toBe(true)
  })

  it('shows the short id after a failed labels read', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([FROM_GONE])),
      http.get('/api/chatrooms/cr_1/agent-labels', () => HttpResponse.json({ status: 502 }, { status: 502 })),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()

    expect(bubbleFor(wrapper, 'm_gone').props('senderName')).toBe('a_gone'.slice(0, 8))
  })

  // Code review finding 6: only a released observation discloses an observer,
  // as on the server; the key on another row asks nothing.
  it('does not ask about an observer id outside a released observation', async () => {
    server.use(http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([])))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1', queryClient: qc })
    await settle()
    const before = labelReads

    qc.setQueryData<Message[]>(convKeys.messages('cr_1'), (prev) => [
      ...(prev ?? []),
      message('m_summary', { sender_type: 'system', metadata: { type: 'summary', observer_agent_id: 'a_x' } }),
    ])
    await settle()

    expect(labelReads).toBe(before)
  })
})

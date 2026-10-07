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

  it('labels search hits with the names the room view knows', async () => {
    labels = [{ agent_id: 'a_unbound', name: 'Tutor' }]
    server.use(
      http.get('/api/chatrooms/cr_1/members', () =>
        HttpResponse.json([{ user_id: 'u_2', display_name: 'Teacher', kind: 'member' }]),
      ),
    )
    const wrapper = await renderView(ChatroomView, { routes, initialRoute: '/chatrooms/cr_1' })
    await settle()
    wrapper.findComponent(ChatroomHeader).vm.$emit('search')
    await settle()

    const panel = wrapper.findComponent(ChatroomSearchPanel)
    expect(panel.props('agentNames')).toMatchObject({ a_unbound: 'Tutor' })
    expect(panel.props('userNames')).toMatchObject({ u_2: 'Teacher' })
  })
})

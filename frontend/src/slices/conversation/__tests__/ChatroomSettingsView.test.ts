import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import { renderView } from '../../../../tests/utils'
import ChatroomSettingsView from '../views/ChatroomSettingsView.vue'
import type { Chatroom } from '../types'

const routes = [
  {
    path: '/chatrooms/:chatroomId/settings',
    name: 'conversation.chatroom.settings',
    component: ChatroomSettingsView,
  },
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: { template: '<div />' },
  },
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: { template: '<div />' },
  },
]

function makeChatroom(overrides: Partial<Chatroom> = {}): Chatroom {
  return {
    id: 'cr_1',
    workspace_id: 'ws_1',
    name: 'Room One',
    allow_org_members: false,
    allow_project_members: true,
    allow_project_owners_only: false,
    allow_guest_links: false,
    guest_token: 'tok_1',
    version: 1,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

function seededClient(rooms: Chatroom[]): QueryClient {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  // Match the watchEffect lookup: queryKey starting with ['conversation', 'chatrooms'].
  qc.setQueryData(['conversation', 'chatrooms', rooms[0]!.workspace_id], rooms)
  return qc
}

describe('ChatroomSettingsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the settings form once chatroom data loads', async () => {
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    await flushPromises()
    // The form renders only after `loadRoom` resolves `room` from the cache.
    expect(wrapper.find('form').exists()).toBe(true)
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe(
      'Room One',
    )
  })
})

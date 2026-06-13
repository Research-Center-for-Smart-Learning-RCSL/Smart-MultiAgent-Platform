import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ChatroomSettingsView from '../views/ChatroomSettingsView.vue'
import type { Chatroom } from '../types'

function makeAgent(id: string, name: string): Record<string, unknown> {
  return {
    id,
    project_id: 'proj_1',
    name,
    model_hint: 'claude',
    key_group_id: 'kg_1',
    system_prompt: '',
    prompt_strategy: 'full',
    rag_config_id: null,
    graphrag_config_id: null,
    context_mode: 'general',
    context_token_cap: null,
    a2a_enabled: false,
    wakeup_config: {},
    workflow_capabilities: {},
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
  }
}

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

  it('lists bound agents and offers unbound ones in the picker', async () => {
    // proj_1 has two agents; only agent_1 is bound to this room.
    server.use(
      http.get('/api/projects/:projectId/agents', () =>
        HttpResponse.json([
          makeAgent('agent_1', 'Bound Agent'),
          makeAgent('agent_2', 'Free Agent'),
        ]),
      ),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json([{ agent_id: 'agent_1' }]),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    await flushPromises()
    await flushPromises()

    // Bound agent is shown with an unbind control (one per bound row).
    expect(wrapper.text()).toContain('Bound Agent')
    expect(wrapper.findAll('.agent-head button')).toHaveLength(1)

    // The picker offers only the still-unbound agent.
    const optionValues = wrapper
      .findAll('select option')
      .map((o) => (o.element as HTMLOptionElement).value)
    expect(optionValues).toContain('agent_2')
    expect(optionValues).not.toContain('agent_1')
  })

  it('binds the selected agent via POST and refreshes', async () => {
    let posted: string | null = null
    server.use(
      http.get('/api/projects/:projectId/agents', () =>
        HttpResponse.json([makeAgent('agent_2', 'Free Agent')]),
      ),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json(posted ? [{ agent_id: posted }] : []),
      ),
      http.post('/api/chatrooms/:chatroomId/agents', async ({ request }) => {
        const body = (await request.json()) as { agent_id: string }
        posted = body.agent_id
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    await flushPromises()
    await flushPromises()

    await wrapper.find('select').setValue('agent_2')
    await wrapper.find('.agent-add').trigger('submit')
    await flushPromises()
    await flushPromises()

    expect(posted).toBe('agent_2')
    // After the refresh, the agent moves out of the picker into the bound list.
    expect(wrapper.text()).toContain('Free Agent')
  })

  it('renders a removable row for a bound agent missing from the project list', async () => {
    // The bound agent was soft-deleted, so the project list omits it.
    server.use(
      http.get('/api/projects/:projectId/agents', () => HttpResponse.json([])),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json([{ agent_id: 'agent_gone_1234' }]),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    await flushPromises()
    await flushPromises()

    // Orphan binding is surfaced (by id prefix) with an unbind control, not
    // swallowed by the "no agents" message.
    expect(wrapper.text()).toContain('agent_go')
    expect(wrapper.findAll('.agent-head button')).toHaveLength(1)
  })

  it('hides the wakeup editor for a malformed config without crashing', async () => {
    // `triggers` present but missing the sub-objects the editor dereferences.
    const partial = makeAgent('agent_1', 'Partial Agent')
    partial.wakeup_config = { triggers: {} }
    server.use(
      http.get('/api/projects/:projectId/agents', () =>
        HttpResponse.json([partial]),
      ),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json([{ agent_id: 'agent_1' }]),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    await flushPromises()
    await flushPromises()

    // Row renders, but the editor is guarded out (would otherwise throw).
    expect(wrapper.text()).toContain('Partial Agent')
    expect(wrapper.find('.wakeup-editor').exists()).toBe(false)
  })
})

import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { useSessionStore } from '@shared/stores/session'
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
    rag_config_id: null,
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
    // `loadRoom` paints from the cache and then revalidates, so the GET has to
    // agree with the seeded room or the assertion below is testing the
    // fallback handler's room instead.
    server.use(http.get('/api/chatrooms/:id', () => HttpResponse.json(makeChatroom())))
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

  it('renders the chatroom Concept Map panel with the inherited-access note (AC-3)', async () => {
    // Concept Map is chatroom-owned; access is inherited from the room, so the
    // panel carries an info note and no privacy toggle.
    server.use(
      http.get('/api/workspaces/:workspaceId', () =>
        HttpResponse.json({
          id: 'ws_1',
          project_id: 'proj_1',
          name: 'WS One',
          concept_map_enabled: true,
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
      http.get('/api/projects/:projectId/agents', () => HttpResponse.json([])),
      http.get('/api/chatrooms/:chatroomId/agents', () => HttpResponse.json([])),
      http.get('/api/projects/:projectId/graphrag-configs', () => HttpResponse.json([])),
      http.get('/api/projects/:projectId/key-groups', () => HttpResponse.json([])),
      http.get('/api/projects/:projectId/members', () =>
        HttpResponse.json([
          { user_id: 'u_1', email: 'u@smap.test', role: 'owner', joined_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([makeChatroom()]),
    })
    const session = useSessionStore()
    session.me = { id: 'u_1', email: 'u@smap.test', email_verified: true, is_admin: false, status: 'active' }
    await flushPromises()
    await flushPromises()
    await flushPromises()

    // The inherited-access note and the shared panel are both present.
    expect(wrapper.text()).toContain('conversation.conceptMap.chatroomInheritsAccess')
    expect(wrapper.text()).toContain('agents.conceptMapPanel.title')
    // No wide-layer privacy toggle on the chatroom (access is inherited).
    expect(wrapper.text()).not.toContain('conversation.conceptMap.workspacePrivacyLabel')
  })

  it('normalizes a malformed config instead of hiding the wakeup editor', async () => {
    // `triggers` present but missing the sub-objects the editor dereferences —
    // normalizeWakeupConfig fills them with defaults rather than the editor
    // being hidden (which would leave the agent's wakeup unconfigurable from
    // this page even though AgentDetailView shows it fine for the same data).
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

    expect(wrapper.text()).toContain('Partial Agent')
    expect(wrapper.find('.wakeup-editor').exists()).toBe(true)
  })

  it('keeps the Guest Link card hidden when enabling guest links was rejected (F-7)', async () => {
    // The card is the user-visible half of F-7: gated on the toggle's own
    // state, it appeared — and started fetching a link — for a room the server
    // had left closed.
    const room = makeChatroom({ allow_guest_links: false })
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(room)),
      http.patch('/api/chatrooms/:id', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/internal', title: 'Internal', status: 500 },
          { status: 500 },
        ),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([room]),
    })
    await flushPromises()

    // Located by its label, not by index: the access section gains tiers over
    // time (section 13.2a added one between these two), and a positional click
    // silently starts exercising a different toggle when it does.
    const guestRow = wrapper
      .findAll('.access-row')
      .find((row) => row.text().includes('conversation.settings.allowGuestLinks'))
    expect(guestRow).toBeTruthy()
    await guestRow!.find('button[role="switch"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('conversation.settings.guestLinkLabel')
  })

  it('puts a group checkbox back on the server state when the bind fails', async () => {
    // The checkbox is uncontrolled: the browser flips `el.checked` on click, and
    // `:checked` re-applies only when the rendered value changes. After a failed
    // bind the confirmed set is by definition unchanged, so nothing patched the
    // DOM and the box kept the click — a room access control displaying a
    // binding the server never accepted, under a toast saying it failed.
    const room = makeChatroom({ allow_project_members: false, allow_member_groups: true })
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(room)),
      http.get('/api/workspaces/:id', () =>
        HttpResponse.json({ id: 'ws_1', project_id: 'proj_1', name: 'WS' }),
      ),
      http.get('/api/projects/:id/member-groups', () =>
        HttpResponse.json([
          {
            id: 'mg_1',
            project_id: 'proj_1',
            name: 'Group One',
            version: 1,
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
      http.get('/api/chatrooms/:id/member-groups', () =>
        HttpResponse.json({ member_group_ids: [] }),
      ),
      http.put('/api/chatrooms/:id/member-groups', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/internal', title: 'Internal', status: 500 },
          { status: 500 },
        ),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([room]),
    })
    await flushPromises()

    const box = wrapper.find('.group-picker input[type="checkbox"]')
    expect(box.exists()).toBe(true)
    expect((box.element as HTMLInputElement).checked).toBe(false)

    await box.setValue(true)
    await flushPromises()

    // Re-query: the fix re-creates the input, so the original handle is detached.
    const after = wrapper.find('.group-picker input[type="checkbox"]')
    expect((after.element as HTMLInputElement).checked).toBe(false)
  })

  it('refuses to render the group picker when the bound set could not be read', async () => {
    // A failed query reports `isLoading: false` with no data, and the bound set
    // falls back to `[]`. Rendering on that drew every box unchecked, and since
    // the endpoint REPLACES, one click would have wiped the room's real
    // bindings — silently, and the write-back would have recorded the loss as
    // confirmed. The picker must be unreachable from "no answer yet".
    const room = makeChatroom({ allow_project_members: false, allow_member_groups: true })
    server.use(
      http.get('/api/chatrooms/:id', () => HttpResponse.json(room)),
      http.get('/api/workspaces/:id', () =>
        HttpResponse.json({ id: 'ws_1', project_id: 'proj_1', name: 'WS' }),
      ),
      http.get('/api/projects/:id/member-groups', () =>
        HttpResponse.json([
          {
            id: 'mg_1',
            project_id: 'proj_1',
            name: 'Group One',
            version: 1,
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
      http.get('/api/chatrooms/:id/member-groups', () =>
        HttpResponse.json(
          { type: 'https://smap.local/problems/internal', title: 'Internal', status: 500 },
          { status: 500 },
        ),
      ),
    )
    const wrapper = await renderView(ChatroomSettingsView, {
      routes,
      initialRoute: '/chatrooms/cr_1/settings',
      queryClient: seededClient([room]),
    })
    await flushPromises()

    expect(wrapper.find('.group-picker').exists()).toBe(false)
    expect(wrapper.text()).toContain('conversation.settings.boundGroupsLoadFailed')
  })
})

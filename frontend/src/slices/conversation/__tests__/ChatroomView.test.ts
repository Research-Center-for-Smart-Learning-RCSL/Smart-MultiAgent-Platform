import { describe, it, expect, vi, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ChatroomView from '../views/ChatroomView.vue'
import { useConversationStore } from '../stores/conversation'
import { useSessionStore } from '@shared/stores/session'
import { useConfirmDialog } from '@shared/composables'

const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>
  return { ...actual, useToast: () => mockToast }
})

const routes = [
  {
    path: '/chatrooms/:chatroomId',
    name: 'conversation.chatroom',
    component: ChatroomView,
  },
]

function signInAs(userId: string, isAdmin = false): void {
  const session = useSessionStore()
  session.me = {
    id: userId,
    email: 'u@smap.test',
    email_verified: true,
    is_admin: isAdmin,
    status: 'active',
  }
}

async function settle(): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await nextTick()
}

describe('ChatroomView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains the message list and composer form', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    expect(wrapper.find('.chatroom').exists()).toBe(true)
    expect(wrapper.find('form.composer').exists()).toBe(true)
    expect(wrapper.find('ol.messages').exists()).toBe(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows edit/delete affordances on the user\'s own recent message', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_1',
            content_md: 'hello',
            metadata: {},
            version: 1,
            created_at: new Date(Date.now() - 1000).toISOString(),
            edited_at: null,
            deleted_at: null,
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()
    // Edit + Delete hover actions on the own, within-window message.
    expect(wrapper.find('.msg-action--edit').exists()).toBe(true)
    expect(wrapper.find('.msg-action--delete').exists()).toBe(true)
  })

  it('shows edit/delete to a non-admin moderator on another user\'s aged message (V-4)', async () => {
    // The affordances render through the real ChatroomView -> message bubble
    // chain, driven only by ChatroomOut.is_moderator: the signed-in user is
    // not an admin, does not own the message, and the five-minute author
    // window closed long ago.
    server.use(
      http.get('/api/chatrooms/:chatroomId', () =>
        HttpResponse.json({
          id: 'cr_1', name: 'Test Room', workspace_id: 'ws_1',
          allow_org_members: false, allow_project_members: true,
          allow_project_owners_only: false, allow_guest_links: false,
          version: 1, created_at: '2026-01-01T00:00:00Z', deleted_at: null,
          created_by_user_id: 'u_other', disclose_observers: true,
          observers_present: false, is_moderator: true,
        }),
      ),
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_other',
            content_md: 'posted an hour ago',
            metadata: {},
            version: 1,
            created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
            edited_at: null,
            deleted_at: null,
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()

    expect(wrapper.find('.msg-action--edit').exists()).toBe(true)
    expect(wrapper.find('.msg-action--delete').exists()).toBe(true)
  })

  it('hides edit/delete from a plain member on another user\'s aged message', async () => {
    // Same fixture with the moderator bit off — the over-grant guard.
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_other',
            content_md: 'posted an hour ago',
            metadata: {},
            version: 1,
            created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
            edited_at: null,
            deleted_at: null,
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()

    expect(wrapper.find('.msg-action--edit').exists()).toBe(false)
    expect(wrapper.find('.msg-action--delete').exists()).toBe(false)
  })

  it('renders message attachments: download for active, placeholder for expired', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json([
          {
            id: 'm_1',
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_1',
            content_md: 'see files',
            metadata: {},
            version: 1,
            created_at: '2026-01-01T00:00:00Z',
            edited_at: null,
            deleted_at: null,
            attachments: [
              {
                id: 'att_1',
                chatroom_id: 'cr_1',
                message_id: 'm_1',
                filename: 'report.pdf',
                mime: 'application/pdf',
                size_bytes: 10,
                status: 'active',
                scan_status: 'clean',
              },
              {
                id: 'att_2',
                chatroom_id: 'cr_1',
                message_id: 'm_1',
                filename: 'old.png',
                mime: 'image/png',
                size_bytes: 10,
                status: 'expired',
                scan_status: 'clean',
              },
            ],
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    await settle()
    expect(wrapper.text()).toContain('report.pdf') // active → download button
    expect(wrapper.find('.attachment-gone').exists()).toBe(true) // expired → placeholder
  })

  it('surfaces export status after triggering an export from the modal', async () => {
    server.use(
      http.post('/api/chatrooms/cr_1/export', () =>
        HttpResponse.json({ job_id: 'job_1', status: 'queued' }),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    // Open the export modal, then submit; the modal switches to a status view.
    await wrapper.find('[data-testid="open-export"]').trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="submit-export"]').trigger('click')
    await settle()
    expect(wrapper.find('[data-testid="export-status"]').exists()).toBe(true)
  })

  it('reopening the export modal cancels the in-flight poller (F-16)', async () => {
    // The job never reaches a terminal state, so its poller keeps ticking and
    // would otherwise repopulate the freshly-reopened modal.
    server.use(
      http.post('/api/chatrooms/cr_1/export', () =>
        HttpResponse.json({ job_id: 'job_1', status: 'queued' }),
      ),
      http.get('/api/exports/job_1', () =>
        HttpResponse.json({
          job_id: 'job_1',
          chatroom_id: 'cr_1',
          status: 'running',
          url: null,
          error: null,
        }),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })

    // Run job A; the modal switches to its running status view.
    await wrapper.find('[data-testid="open-export"]').trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="submit-export"]').trigger('click')
    await settle()
    expect(wrapper.find('[data-testid="export-status"]').exists()).toBe(true)

    // Reopen the modal to configure a new export; it must return to the form.
    await wrapper.find('[data-testid="open-export"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="export-status"]').exists()).toBe(false)

    // Let a full poll interval (3s default) elapse: the superseded job's tick
    // must not render back into the modal.
    await new Promise((r) => setTimeout(r, 3200))
    await nextTick()
    expect(wrapper.find('[data-testid="export-status"]').exists()).toBe(false)
  })

  it('renders the Observer tab for a creator whose observations outlived the last observer binding', async () => {
    server.use(
      http.get('/api/chatrooms/:chatroomId', () =>
        HttpResponse.json({
          id: 'cr_1', name: 'Test Room', project_id: 'proj_1',
          workspace_id: 'ws_1',
          allow_org_members: false, allow_project_members: true,
          allow_project_owners_only: false, allow_guest_links: false,
          created_by_user_id: 'u_1',
          agents: [],
        }),
      ),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json([{ agent_id: 'agent_normal', role: 'normal' }]),
      ),
      http.get('/api/chatrooms/:chatroomId/observations', () =>
        HttpResponse.json([
          {
            id: 'o1',
            chatroom_id: 'cr_1',
            agent_id: 'agent_gone',
            content_md: 'stranded analysis',
            metadata: {},
            trigger: 'every_n_messages',
            trigger_message_id: null,
            released_at: null,
            release_target: null,
            released_by_user_id: null,
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()

    const observerTab = wrapper
      .findAll('[role="tab"]')
      .find((t) => t.text() === 'conversation.observers.tab')
    expect(observerTab?.exists()).toBe(true)
    await observerTab?.trigger('click')
    await settle()

    expect(wrapper.find('.obs-panel').exists()).toBe(true)
    expect(wrapper.text()).toContain('stranded analysis')
  })

  it('falls back to the People tab instead of going blank when the last stranded observation is deleted', async () => {
    let deleted = false
    server.use(
      http.get('/api/chatrooms/:chatroomId', () =>
        HttpResponse.json({
          id: 'cr_1', name: 'Test Room', project_id: 'proj_1',
          workspace_id: 'ws_1',
          allow_org_members: false, allow_project_members: true,
          allow_project_owners_only: false, allow_guest_links: false,
          created_by_user_id: 'u_1',
          agents: [],
        }),
      ),
      http.get('/api/chatrooms/:chatroomId/agents', () =>
        HttpResponse.json([{ agent_id: 'agent_normal', role: 'normal' }]),
      ),
      http.get('/api/chatrooms/:chatroomId/observations', () =>
        HttpResponse.json(
          deleted
            ? []
            : [
                {
                  id: 'o1',
                  chatroom_id: 'cr_1',
                  agent_id: 'agent_gone',
                  content_md: 'stranded analysis',
                  metadata: {},
                  trigger: 'every_n_messages',
                  trigger_message_id: null,
                  released_at: null,
                  release_target: null,
                  released_by_user_id: null,
                  created_at: '2026-01-01T00:00:00Z',
                },
              ],
        ),
      ),
      http.delete('/api/chatrooms/:chatroomId/observations/:observationId', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    signInAs('u_1')
    await settle()

    const observerTab = wrapper
      .findAll('[role="tab"]')
      .find((t) => t.text() === 'conversation.observers.tab')
    await observerTab?.trigger('click')
    await settle()
    expect(wrapper.find('.obs-panel').exists()).toBe(true)

    const deleteBtn = wrapper.find('[aria-label="conversation.observers.delete"]')
    expect(deleteBtn.exists()).toBe(true)
    await deleteBtn.trigger('click')
    // onObservationDelete awaits useConfirmDialog().confirm() before deleting —
    // resolve it the same way a user clicking the dialog's confirm button would.
    const { handleConfirm } = useConfirmDialog()
    handleConfirm()
    await settle()
    await settle()

    // The Observer tab (and its panel) must be gone — this is the last
    // observation and no observer is bound — but the rail must not go blank:
    // railTab has to fall back to a tab that still exists.
    expect(wrapper.find('.obs-panel').exists()).toBe(false)
    expect(
      wrapper.findAll('[role="tab"]').find((t) => t.text() === 'conversation.observers.tab'),
    ).toBeUndefined()
    expect(wrapper.find('[role="tabpanel"]:not([hidden])').exists()).toBe(true)
  })

  it('renders the streaming draft bubble while agent tokens accumulate', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    const store = useConversationStore()
    expect(wrapper.find('[data-testid="streaming-draft"]').exists()).toBe(false)

    store.appendAgentToken('cr_1', 'a1', 'Hello **wor')
    store.appendAgentToken('cr_1', 'a1', 'ld**')
    await nextTick()
    const bubble = wrapper.find('[data-testid="streaming-draft"]')
    expect(bubble.exists()).toBe(true)
    expect(bubble.find('.md').html()).toContain('<strong>world</strong>')

    // Cleared when the persisted message arrives (socket calls this).
    store.clearAgentStream('cr_1', 'a1')
    await nextTick()
    expect(wrapper.find('[data-testid="streaming-draft"]').exists()).toBe(false)
  })

  it('toasts and clears the store flag when an agent error is surfaced', async () => {
    mockToast.error.mockClear()
    await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    const store = useConversationStore()

    store.setAgentError('cr_1', 'provider_error')
    await nextTick()
    expect(mockToast.error).toHaveBeenCalledTimes(1)
    expect(store.agentError['cr_1']).toBeNull()

    store.setAgentError('cr_1', 'timeout')
    await nextTick()
    expect(mockToast.error).toHaveBeenCalledTimes(2)
    expect(store.agentError['cr_1']).toBeNull()
  })

  it('does not render the empty state while the first fetch is pending (F-17)', async () => {
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    // Before settle(): the GET is still in flight, so this must read as
    // "loading", never as "genuinely empty".
    expect(wrapper.find('.s-empty-state').exists()).toBe(false)
    expect(wrapper.find('.chatroom__messages-skeleton').exists()).toBe(true)

    await settle()
    expect(wrapper.find('.chatroom__messages-skeleton').exists()).toBe(false)
    expect(wrapper.find('.s-empty-state').exists()).toBe(true)
  })

  it('does not render "Load earlier" before the query settles (F-17)', async () => {
    server.use(
      http.get('/api/chatrooms/cr_1/messages', () =>
        HttpResponse.json(
          Array.from({ length: 100 }, (_, i) => ({
            id: `m_${i}`,
            chatroom_id: 'cr_1',
            sender_type: 'user',
            sender_id: 'u_1',
            content_md: `message ${i}`,
            metadata: {},
            version: 1,
            created_at: new Date(Date.now() - (100 - i) * 1000).toISOString(),
            edited_at: null,
            deleted_at: null,
          })),
        ),
      ),
    )
    const wrapper = await renderView(ChatroomView, {
      routes,
      initialRoute: '/chatrooms/cr_1',
    })
    expect(wrapper.find('.load-earlier').exists()).toBe(false)

    await settle()
    expect(wrapper.find('.load-earlier').exists()).toBe(true)
  })
})

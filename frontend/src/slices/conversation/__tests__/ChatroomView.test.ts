import { describe, it, expect, vi, afterEach } from 'vitest'
import type { VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ChatroomView from '../views/ChatroomView.vue'
import { useConversationStore } from '../stores/conversation'
import { useSessionStore } from '@shared/stores/session'
import { useOrchestrationStore } from '@shared/stores/orchestration'
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

    // Wait for the condition, not for a fixed 100ms: rendering 100 messages
    // (each through markdown) overruns that budget under full-suite load, and
    // the control then reads as "still absent" for a reason the test is not
    // about. The assertion above is the F-17 guard and stays synchronous.
    await vi.waitFor(() => {
      expect(wrapper.find('.load-earlier').exists()).toBe(true)
    })
  })

  // jsdom performs no layout, so these assert the structural contract that makes
  // the rail scrollable and resizable — which element owns the width, and which
  // opts into the fill height — rather than that clipping stopped. The visual
  // outcome is verified by hand (spec §12).
  describe('desktop right rail', () => {
    const originalWidth = window.innerWidth

    function setViewport(width: number): void {
      Object.defineProperty(window, 'innerWidth', {
        value: width, configurable: true, writable: true,
      })
    }

    afterEach(() => {
      setViewport(originalWidth)
      localStorage.clear()
    })

    it('drives the rail track from the persisted width', async () => {
      setViewport(1400)
      localStorage.setItem('smap-chatroom-rail-w', '420')
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      expect(wrapper.find('.chatroom').attributes('style')).toContain('--chatroom-rail-w: 420px')
    })

    it('exposes the rail handle as a separator on desktop', async () => {
      setViewport(1400)
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      const handle = wrapper.find('.chatroom__rail-handle')
      expect(handle.exists()).toBe(true)
      expect(handle.attributes('role')).toBe('separator')
    })

    // AC-10 — below lg the rail is a drawer, which scrolls on its own.
    it('offers no resize handle below the desktop breakpoint', async () => {
      setViewport(800)
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      expect(wrapper.find('.chatroom__rail-handle').exists()).toBe(false)
      expect(wrapper.find('.chatroom').attributes('style') ?? '')
        .not.toContain('--chatroom-rail-w')
    })

    // AC-2 / AC-3 — the tab strip stays put and the panel below it is what
    // scrolls, which only holds if the rail's STabs is in fill mode.
    it('opts the tabbed rail into filling its height', async () => {
      setViewport(1400)
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
      )
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      expect(wrapper.find('[role="tablist"]').exists()).toBe(true)
      expect(wrapper.find('.s-tabs--fill').exists()).toBe(true)
    })
  })

  // T-8 of docs/tasks/2026-08-19-chatroom-scroll-and-composer (F-47).
  //
  // Approval cards were a second v-for after the message list, so their
  // position was list order -- and `getApprovalsForRoom` returns
  // `Object.values(...)`, which is insertion order, not time order. The card
  // therefore appended below every message regardless of when the gate was
  // raised, and after resolution stayed pinned there.
  describe('approval cards in the feed (F-47)', () => {
    function message(id: string, createdAt: string) {
      return {
        id,
        chatroom_id: 'cr_1',
        sender_type: 'user',
        sender_id: 'u_1',
        content_md: id,
        metadata: {},
        version: 1,
        created_at: createdAt,
        edited_at: null,
        deleted_at: null,
      }
    }

    function approval(id: string, startedAt: string) {
      return {
        id,
        workflow_run_id: 'run_1',
        mode: 'single' as const,
        leader_agent_id: 'a1',
        approver_agent_ids: ['a1'],
        timeout_seconds: 300,
        state: 'pending' as const,
        started_at: startedAt,
        ended_at: null,
        votes: [],
      }
    }

    /** Label each feed item by kind, in render order.
     *
     *  Matched by the two item markers rather than by child position: the
     *  harness stubs TransitionGroup, so the items are not direct children of
     *  `ol.messages` here even though they are in the real DOM.
     *  querySelectorAll returns document order, so the interleaving is real. */
    function feedOrder(wrapper: VueWrapper): string[] {
      return wrapper
        .findAll('ol.messages [id^="msg-"], ol.messages [data-testid="feed-approval"]')
        .map((el) =>
          el.attributes('data-testid') === 'feed-approval'
            ? 'approval'
            : (el.attributes('id') ?? 'other'),
        )
    }

    it('places the card at the point in the conversation where it was raised', async () => {
      server.use(
        http.get('/api/chatrooms/cr_1/messages', () =>
          HttpResponse.json([
            message('m_before', '2026-01-01T00:00:00.000Z'),
            message('m_after', '2026-01-01T00:00:02.000Z'),
          ]),
        ),
      )
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      const orch = useOrchestrationStore()
      orch.upsertApproval('cr_1', approval('ap_1', '2026-01-01T00:00:01.000Z'))
      await settle()

      // Before the fix this was msg-m_before, msg-m_after, approval.
      expect(feedOrder(wrapper)).toEqual(['msg-m_before', 'approval', 'msg-m_after'])
    })

    it('orders two cards by when each gate was raised, not by insertion', async () => {
      server.use(
        http.get('/api/chatrooms/cr_1/messages', () =>
          HttpResponse.json([message('m_mid', '2026-01-01T00:00:02.000Z')]),
        ),
      )
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      const orch = useOrchestrationStore()
      // Inserted newest-first, so insertion order and time order disagree.
      orch.upsertApproval('cr_1', approval('ap_late', '2026-01-01T00:00:03.000Z'))
      orch.upsertApproval('cr_1', approval('ap_early', '2026-01-01T00:00:01.000Z'))
      await settle()

      expect(feedOrder(wrapper)).toEqual(['approval', 'msg-m_mid', 'approval'])
    })

    it('keeps an empty room empty when it has neither messages nor approvals', async () => {
      server.use(http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([])))
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      expect(wrapper.find('.chatroom__empty').exists()).toBe(true)
    })

    it('drops the empty state once an approval alone occupies the feed', async () => {
      server.use(http.get('/api/chatrooms/cr_1/messages', () => HttpResponse.json([])))
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      const orch = useOrchestrationStore()
      orch.upsertApproval('cr_1', approval('ap_1', '2026-01-01T00:00:01.000Z'))
      await settle()

      expect(wrapper.find('.chatroom__empty').exists()).toBe(false)
      expect(feedOrder(wrapper)).toEqual(['approval'])
    })
  })

  // T-9 of docs/tasks/2026-08-19-chatroom-scroll-and-composer (F-29, first arm).
  //
  // useBreakpoint exposes three bands and the chatroom's layout needs four, so
  // 1024-1279 had no expression at all: it got the full desktop grid, where
  // 220 + 10 + 200 = 430px of fixed chrome left the feed under 600px with no
  // way to collapse either rail.
  //
  // Class and presence assertions only. jsdom computes no layout, so where the
  // overlay panels land is AC-14's browser half.
  describe('compact desktop band, 1024-1279 (F-29)', () => {
    const originalWidth = window.innerWidth

    function setViewport(width: number): void {
      Object.defineProperty(window, 'innerWidth', {
        value: width, configurable: true, writable: true,
      })
    }

    afterEach(() => {
      setViewport(originalWidth)
      localStorage.clear()
    })

    async function atWidth(width: number) {
      setViewport(width)
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()
      return wrapper
    }

    it('binds the compact class and drops the resize handle at 1100', async () => {
      const wrapper = await atWidth(1100)

      expect(wrapper.find('.chatroom--compact').exists()).toBe(true)
      // An overlay panel has no track to size, so the handle has nothing to do.
      expect(wrapper.find('.chatroom__rail-handle').exists()).toBe(false)
    })

    it('gives the compact band the toggles that open its overlay panels', async () => {
      // Without these the panels would be unreachable: the agents toggle is
      // otherwise mobile-only and the people toggle otherwise below-desktop.
      const wrapper = await atWidth(1100)

      const labels = wrapper.findAll('button[aria-label]')
        .map((b) => b.attributes('aria-label'))
      expect(labels).toContain('conversation.chatroom.agents')
      expect(labels).toContain('conversation.chatroom.people')
    })

    it('opens and closes a panel from the same header toggle', async () => {
      const wrapper = await atWidth(1100)
      const agentsToggle = wrapper.findAll('button[aria-label]')
        .find((b) => b.attributes('aria-label') === 'conversation.chatroom.agents')!

      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(false)
      await agentsToggle.trigger('click')
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(true)
      await agentsToggle.trigger('click')
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(false)
    })

    it('leaves the full three-column layout untouched at 1400', async () => {
      const wrapper = await atWidth(1400)

      expect(wrapper.find('.chatroom--compact').exists()).toBe(false)
      expect(wrapper.find('.chatroom__rail-handle').exists()).toBe(true)
      expect(wrapper.find('.chatroom__agents').exists()).toBe(true)
      expect(wrapper.find('.chatroom__presence').exists()).toBe(true)
    })

    // Q-8 was CUT from this dossier at approval and deferred to FU-6: moving the
    // agent rail out of 768-1023 is the one change here that takes a surface
    // away from users rather than restoring one. This pins the deferral, so a
    // later edit to the compact band cannot quietly carry it along.
    it('does not touch the tablet band, whose rail change was deferred', async () => {
      const wrapper = await atWidth(800)

      expect(wrapper.find('.chatroom--compact').exists()).toBe(false)
      expect(wrapper.find('.chatroom--tablet').exists()).toBe(true)
      // Still in the grid at 768-1023, exactly as before this dossier.
      expect(wrapper.find('.chatroom__agents').exists()).toBe(true)
    })
  })
})

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

// Read for the CSS assertions below: jsdom applies no scoped styles and
// computes no layout, so the stylesheet source is the only thing to assert
// against. Same idiom as ChatroomSearchPanel.test.ts.
const viewSource = Object.values(
  import.meta.glob('/src/slices/conversation/views/ChatroomView.vue', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Record<string, string>,
)[0]

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

    // Code review, finding 1. `NaN <= x` is false for EVERY x, including the
    // final drain at +Infinity, so a single unparseable timestamp stranded its
    // own card and every approval sorted after it. orchestration.ts:65 already
    // guards the same field with Number.isFinite, so the case is reachable.
    it('still renders an approval whose timestamp cannot be parsed', async () => {
      server.use(
        http.get('/api/chatrooms/cr_1/messages', () =>
          HttpResponse.json([message('m_1', '2026-01-01T00:00:00.000Z')]),
        ),
      )
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      const orch = useOrchestrationStore()
      orch.upsertApproval('cr_1', approval('ap_bad', 'not-a-timestamp'))
      orch.upsertApproval('cr_1', approval('ap_good', '2026-01-01T00:00:02.000Z'))
      await settle()

      // Both present: the bad one at the tail rather than swallowed, and the
      // good one not stranded behind it.
      expect(feedOrder(wrapper)).toEqual(['msg-m_1', 'approval', 'approval'])
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

  // Security gate, dimension 12. Scroll-based pagination turns a click loop into
  // an automatic one: a page that comes back entirely duplicates leaves the
  // feed, the cursor and hasOlderMessages all unchanged, so the sentinel keeps
  // intersecting and the same request is reissued forever. A human clicking the
  // button cannot produce that.
  describe('history auto-trigger does not loop on a page that adds nothing', () => {
    const originalIO = globalThis.IntersectionObserver
    let lastCallback: IntersectionObserverCallback | null = null

    class StubIO {
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = vi.fn()
      takeRecords = vi.fn()
      constructor(cb: IntersectionObserverCallback) {
        lastCallback = cb
      }
    }

    afterEach(() => {
      globalThis.IntersectionObserver = originalIO
      lastCallback = null
    })

    function page(prefix: string, n: number) {
      return Array.from({ length: n }, (_, i) => ({
        id: `${prefix}_${i}`,
        chatroom_id: 'cr_1',
        sender_type: 'user',
        sender_id: 'u_1',
        content_md: `${prefix} ${i}`,
        metadata: {},
        version: 1,
        created_at: `2026-01-01T00:00:${String(i).padStart(2, '0')}.000Z`,
        edited_at: null,
        deleted_at: null,
      }))
    }

    it('stops re-requesting once a load adds no new messages', async () => {
      globalThis.IntersectionObserver = StubIO as unknown as typeof IntersectionObserver
      let calls = 0
      // A full page every time, always the rows the client already holds: the
      // dedupe drops all of them, so nothing is prepended and the cursor stands.
      const first = page('m', 100)
      server.use(
        http.get('/api/chatrooms/cr_1/messages', ({ request }) => {
          const url = new URL(request.url)
          if (!url.searchParams.get('before')) return HttpResponse.json(first)
          calls += 1
          return HttpResponse.json(first)
        }),
      )
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
      })
      signInAs('u_1')
      await settle()

      for (let i = 0; i < 5; i++) {
        lastCallback?.(
          [{ isIntersecting: true } as IntersectionObserverEntry],
          {} as IntersectionObserver,
        )
        await settle()
      }

      // One attempt, then the trigger retires itself.
      expect(calls).toBe(1)
      expect(wrapper.exists()).toBe(true)
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

    it('says whether the panel it toggles is open', async () => {
      // Code review, finding 6. Below lg the SDrawer carries this semantics;
      // an overlay panel's toggle has to state it itself.
      const wrapper = await atWidth(1100)
      const agentsToggle = wrapper.findAll('button[aria-label]')
        .find((b) => b.attributes('aria-label') === 'conversation.chatroom.agents')!

      expect(agentsToggle.attributes('aria-expanded')).toBe('false')
      await agentsToggle.trigger('click')
      expect(agentsToggle.attributes('aria-expanded')).toBe('true')
    })

    it('closes an open panel on Escape', async () => {
      // The overlay panels have no scrim and no close button, so without this
      // the only way out is to re-find the toggle.
      const wrapper = await atWidth(1100)
      const agentsToggle = wrapper.findAll('button[aria-label]')
        .find((b) => b.attributes('aria-label') === 'conversation.chatroom.agents')!
      await agentsToggle.trigger('click')
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(true)

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      expect(wrapper.find('.chatroom__panel--open').exists()).toBe(false)
    })

    it('closes an open panel when the viewport leaves the compact band', async () => {
      // The same refs drive an SDrawer below lg and an overlay panel here, so a
      // panel left open would otherwise reappear as an already-open drawer.
      const wrapper = await atWidth(1100)
      const agentsToggle = wrapper.findAll('button[aria-label]')
        .find((b) => b.attributes('aria-label') === 'conversation.chatroom.agents')!
      await agentsToggle.trigger('click')
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(true)

      // 1400 rather than a mobile width on purpose: below md the rail unmounts
      // outright, so the assertion would pass whether or not the ref was reset.
      // Here the element is still rendered, so only a real reset clears it.
      setViewport(1400)
      window.dispatchEvent(new Event('resize'))
      await settle()

      expect(wrapper.find('.chatroom--compact').exists()).toBe(false)
      expect(wrapper.find('.chatroom__agents').exists()).toBe(true)
      expect(wrapper.find('.chatroom__panel--open').exists()).toBe(false)
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

  // T-5 / T-7. Search, agents and people/observer are one transient group
  // wherever more than one of them covers the feed: two of them open at once
  // would mean two backdrops, two focus traps and two restoration targets, and
  // which one a keypress reaches would depend on mount order.
  describe('transient surface coordination (FU-10 / FU-1)', () => {
    const originalWidth = window.innerWidth
    let mounted: VueWrapper | null = null

    function setViewport(width: number): void {
      Object.defineProperty(window, 'innerWidth', {
        value: width, configurable: true, writable: true,
      })
    }

    afterEach(() => {
      mounted?.unmount()
      mounted = null
      setViewport(originalWidth)
      localStorage.clear()
    })

    // Attached to the document because every focus assertion below is
    // meaningless otherwise: jsdom refuses focus to a detached element and
    // reports <body> either way.
    async function atWidth(width: number): Promise<VueWrapper> {
      setViewport(width)
      const wrapper = await renderView(ChatroomView, {
        routes,
        initialRoute: '/chatrooms/cr_1',
        attachTo: document.body,
      })
      mounted = wrapper
      signInAs('u_1')
      await settle()
      return wrapper
    }

    function toggle(wrapper: VueWrapper, label: string) {
      return wrapper.findAll('button[aria-label]')
        .find((b) => b.attributes('aria-label') === `conversation.chatroom.${label}`)!
    }

    /** Click the way a pointer does: the control takes focus, then fires. */
    async function press(button: ReturnType<typeof toggle>): Promise<void> {
      ;(button.element as HTMLElement).focus()
      await button.trigger('click')
      await nextTick()
    }

    it('keeps exactly one surface active at 1100 (AC-4)', async () => {
      const wrapper = await atWidth(1100)

      await press(toggle(wrapper, 'agents'))
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(true)

      await press(toggle(wrapper, 'people'))
      expect(wrapper.find('.chatroom__presence.chatroom__panel--open').exists()).toBe(true)
      expect(wrapper.find('.chatroom__agents.chatroom__panel--open').exists()).toBe(false)

      await press(toggle(wrapper, 'search'))
      expect(wrapper.find('.search-panel').exists()).toBe(true)
      expect(wrapper.find('.chatroom__panel--open').exists()).toBe(false)
    })

    it('hands focus to the newly opened surface rather than restoring (AC-4)', async () => {
      const wrapper = await atWidth(1100)

      await press(toggle(wrapper, 'agents'))
      expect(wrapper.find('.chatroom__agents').element.contains(document.activeElement))
        .toBe(true)

      await press(toggle(wrapper, 'search'))
      // Focus follows the user into the new surface. Restoring the agents
      // toggle here would leave the search panel open behind the user.
      expect(document.activeElement).toBe(wrapper.find('.search-input__field').element)
    })

    it('restores the initiating control on a normal close (AC-4)', async () => {
      const wrapper = await atWidth(1100)
      const agents = toggle(wrapper, 'agents')

      await press(agents)
      expect(document.activeElement).not.toBe(agents.element)

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      expect(wrapper.find('.chatroom__panel--open').exists()).toBe(false)
      expect(document.activeElement).toBe(agents.element)
    })

    it('leaves the active surface alone while a modal is stacked over it', async () => {
      const wrapper = await atWidth(1100)
      const people = toggle(wrapper, 'people')
      await press(people)
      expect(wrapper.find('.chatroom__presence.chatroom__panel--open').exists()).toBe(true)

      // The export modal is opened from the header, but the observation release
      // dialog is opened from inside this very rail. Either way the modal owns
      // Escape and handles it itself; the keypress still bubbles to window, and
      // acting on it here would shut the surface the user cannot see.
      await toggle(wrapper, 'export').trigger('click')
      await nextTick()
      expect(document.querySelector('[aria-modal="true"]')).not.toBeNull()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      expect(wrapper.find('.chatroom__presence.chatroom__panel--open').exists()).toBe(true)
    })

    it('does not strand focus on <body> when the opener will not take it (AC-4)', async () => {
      const wrapper = await atWidth(1100)
      await press(toggle(wrapper, 'agents'))

      // A keyboard hand-off records whatever holds focus, and after the agents
      // trap ran that is inside the surface being handed off FROM -- not the
      // header control that opened it. Ctrl+K is the reachable way in: the
      // compact rails are not modal, so the shortcut is live while one is open.
      const stranded = document.activeElement as HTMLElement
      expect(wrapper.find('.chatroom__agents').element.contains(stranded)).toBe(true)
      document.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
      )
      await nextTick()
      await nextTick()
      expect(wrapper.find('.search-panel').exists()).toBe(true)

      // In the browser the agents panel is `visibility: hidden` by now and
      // refuses focus; jsdom models the same refusal for a detached node.
      // Either way the restore is a no-op and focus falls to <body> unless
      // close() notices that it did not land.
      stranded.remove()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      expect(document.activeElement).not.toBe(document.body)
      expect(document.activeElement).toBe(wrapper.find('.chatroom').element)
    })

    it('closes the active surface from its backdrop (AC-4/AC-5)', async () => {
      const wrapper = await atWidth(1100)
      const agents = toggle(wrapper, 'agents')
      await press(agents)

      const scrim = wrapper.find('.chatroom__scrim')
      expect(scrim.exists()).toBe(true)
      // The scrim reaches the composer for a rail overlay: the panel covers it,
      // so leaving it clickable would let the user type into a room they can no
      // longer see.
      expect(scrim.classes()).toContain('chatroom__scrim--rail')

      await scrim.trigger('click')
      await nextTick()

      expect(wrapper.find('.chatroom__panel--open').exists()).toBe(false)
      expect(document.activeElement).toBe(agents.element)
    })

    it('scopes the search backdrop to the feed (AC-5)', async () => {
      const wrapper = await atWidth(1100)
      await press(toggle(wrapper, 'search'))

      const scrim = wrapper.find('.chatroom__scrim')
      expect(scrim.exists()).toBe(true)
      // 07-conversation.md:750 dims the message feed, not the composer -- a
      // user may well want to keep typing while reading search results.
      expect(scrim.classes()).toContain('chatroom__scrim--search')

      await scrim.trigger('click')
      await nextTick()
      expect(wrapper.find('.search-panel').exists()).toBe(false)
    })

    it('dims at the documented strength rather than multiplying two dims (AC-5)', () => {
      // jsdom applies no scoped styles, so this reads the SFC the way
      // ChatroomSearchPanel's motion tests read theirs. What it guards is a
      // composition bug that no visual assertion would have caught either:
      // `--overlay-backdrop` (0.45) under `opacity: 0.2` composites to 0.09.
      const rule = /\.chatroom__scrim\s*\{([^}]*)\}/.exec(viewSource ?? '')?.[1] ?? ''
      expect(rule).toContain('--overlay-backdrop-inline')
      expect(rule).not.toMatch(/^\s*opacity:/m)
    })

    it('keeps Tab inside an open compact rail panel (AC-4)', async () => {
      const wrapper = await atWidth(1100)
      await press(toggle(wrapper, 'agents'))

      const panel = wrapper.find('.chatroom__agents')
      // The room has no bound agents in this fixture, so the panel has no
      // focusable child at all -- exactly the dead-end case. Tab must be
      // swallowed rather than walking the user out into the covered feed.
      const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
      panel.element.dispatchEvent(event)

      expect(event.defaultPrevented).toBe(true)
    })

    it('does not mount a backdrop or trap Tab at 1400 (AC-13)', async () => {
      const wrapper = await atWidth(1400)

      // No agents/people toggles exist here at all, so the only surface that
      // can be transient is search -- the rails are persistent columns and must
      // stay in the ordinary tab order.
      expect(wrapper.find('.chatroom__scrim').exists()).toBe(false)

      const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
      wrapper.find('.chatroom__presence').element.dispatchEvent(event)
      expect(event.defaultPrevented).toBe(false)

      expect(wrapper.find('.chatroom__rail-handle').exists()).toBe(true)
    })

    it('mounts no compact backdrop in the deferred tablet band (AC-11)', async () => {
      const wrapper = await atWidth(800)
      await press(toggle(wrapper, 'people'))

      // People is an SDrawer here, which brings its own teleported backdrop;
      // the in-chat scrim belongs to the compact band only.
      expect(wrapper.find('.chatroom__scrim').exists()).toBe(false)
      expect(wrapper.find('.chatroom__rail-handle').exists()).toBe(false)
    })

    it('keeps Ctrl+K a toggle now that search takes focus', async () => {
      const wrapper = await atWidth(1400)

      const shortcut = (): void => {
        document.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
        )
      }

      shortcut()
      await nextTick()
      expect(wrapper.find('.search-panel').exists()).toBe(true)
      // Two ticks, not one: the first mounts the panel, and the focus trap only
      // reaches for the field after its own `await nextTick()` -- which is
      // queued behind this test's, so a single tick reads <body> every time.
      await nextTick()
      // The panel focuses its field on open, and the shortcut ignores keys
      // typed into a text field. Without an explicit exemption the second press
      // is swallowed and the shortcut becomes one-way.
      expect(document.activeElement).toBe(wrapper.find('.search-input__field').element)

      shortcut()
      await nextTick()
      expect(wrapper.find('.search-panel').exists()).toBe(false)
    })

    it('makes search and a drawer mutually exclusive below 1024 (AC-10)', async () => {
      const wrapper = await atWidth(800)

      await press(toggle(wrapper, 'search'))
      expect(wrapper.find('.search-panel').exists()).toBe(true)

      await press(toggle(wrapper, 'people'))
      // Two overlapping surfaces below lg would put an SDrawer's modal trap and
      // the search panel's in-page trap on the page at once.
      expect(wrapper.find('.search-panel').exists()).toBe(false)
    })
  })
})

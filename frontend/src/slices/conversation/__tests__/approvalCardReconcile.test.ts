// AC-8 substitute, client half (dossier
// 2026-08-20-orchestration-room-scoped-reads, D-2).
//
// The in-room approval card reconciles against `GET /api/orchestration/
// approvals/{id}` — the id-addressed route this dossier narrowed. An ordinary
// room member is exactly the principal that makes that call, so the dual track
// is load-bearing here: had the route become owner-only, every member's card
// would have frozen `pending` forever, and no other test would have noticed.
//
// The server half (a real member, real rows, real room ACL) is
// `backend/tests/integration/test_orchestration_room_scoped_reads_db.py`.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'

import type { ChannelEvent } from '@shared/transport'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { ApiError } from '@shared/errors'

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []

vi.mock('@shared/transport', () => {
  const channel = {
    subscribe: (_name: string, handler: (ev: ChannelEvent) => void) => {
      subscribedHandlers.push(handler)
      return () => {}
    },
    onStatus: (handler: (connected: boolean) => void) => {
      statusHandlers.push(handler)
      return () => {}
    },
    onDegraded: () => () => {},
    onCapReached: () => () => {},
    connect: () => {},
    disconnect: () => {},
    close: () => {},
    send: () => {},
  }
  return { wsManager: { channel: () => channel, close: () => {}, closeAll: () => {} } }
})

const getApprovalMock = vi.hoisted(() => vi.fn())
vi.mock('@slices/workflow', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getApproval: getApprovalMock,
}))

const listChatroomApprovalsMock = vi.hoisted(() => vi.fn(async () => []))
vi.mock('../api', () => ({
  listMessages: vi.fn(async () => []),
  getMessage: vi.fn(),
  listChatroomApprovals: listChatroomApprovalsMock,
}))

import { useChatroomSocket } from '../composables/useChatroomSocket'

const ROOM = 'cr_1'
const AGENT = 'agent_1'

function staleApproval(over: Partial<ApprovalWithVotes> = {}): ApprovalWithVotes {
  return {
    id: 'ap_1',
    workflow_run_id: 'run_1',
    mode: 'single',
    leader_agent_id: AGENT,
    approver_agent_ids: [AGENT],
    timeout_seconds: 300,
    state: 'pending',
    // Well past its own grace window, so `reconcilePending` actually fetches.
    started_at: '2020-01-01T00:00:00.000Z',
    ended_at: null,
    votes: [],
    ...over,
  }
}

function mountSocket(): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const Host = defineComponent({
    setup() {
      useChatroomSocket(ROOM)
      return () => null
    },
  })
  return mount(Host, { global: { plugins: [pinia, [VueQueryPlugin, { queryClient: qc }]] } })
}

function problem(status: number, type: string): ApiError {
  return new ApiError({ type: `https://smap.local/problems/${type}`, title: 'x', status })
}

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  vi.clearAllMocks()
})

describe('the in-room approval card reconciles for an ordinary room member', () => {
  it('keeps the card when the server serves the approval', async () => {
    wrapper = mountSocket()
    const store = useOrchestrationStore()
    store.upsertApproval(ROOM, staleApproval())

    getApprovalMock.mockResolvedValue(staleApproval({ state: 'approved' }))
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    expect(getApprovalMock).toHaveBeenCalledWith('ap_1')
    const [card] = store.getApprovalsForRoom(ROOM)
    expect(card.state).toBe('approved')
  })

  it('drops the card on a 404 — including the room-gated refusal', async () => {
    // R15.24 answers 404, not 403, when the caller may not read the record's
    // room. That lands on the same branch as "the gate never became durable",
    // which is the behaviour a member who lost room access should see.
    wrapper = mountSocket()
    const store = useOrchestrationStore()
    store.upsertApproval(ROOM, staleApproval())

    getApprovalMock.mockRejectedValue(problem(404, 'not-found'))
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    expect(store.getApprovalsForRoom(ROOM)).toHaveLength(0)
  })

  it('keeps the card on any other failure rather than guessing', async () => {
    wrapper = mountSocket()
    const store = useOrchestrationStore()
    store.upsertApproval(ROOM, staleApproval())

    getApprovalMock.mockRejectedValue(problem(503, 'http-error'))
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    expect(store.getApprovalsForRoom(ROOM).map((a) => a.state)).toEqual(['pending'])
  })
})

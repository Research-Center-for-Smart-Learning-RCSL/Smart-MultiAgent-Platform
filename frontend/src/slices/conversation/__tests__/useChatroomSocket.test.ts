// Streaming/error/timeout behaviour of the chatroom WS subscriber.
//
// The transport layer is mocked; events are injected by invoking the
// wildcard handler the composable registers via channel.subscribe('*', …).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'

import type { ChannelEvent } from '@shared/transport'
import { useActivitiesStore } from '@slices/activities'
import type * as ActivitiesSlice from '@slices/activities'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []
const degradedHandlers: Array<(degraded: boolean) => void> = []
const capReachedHandlers: Array<(capReached: boolean) => void> = []

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
    onDegraded: (handler: (degraded: boolean) => void) => {
      degradedHandlers.push(handler)
      handler(false)
      return () => {}
    },
    onCapReached: (handler: (capReached: boolean) => void) => {
      capReachedHandlers.push(handler)
      handler(false)
      return () => {}
    },
    connect: () => {},
    disconnect: () => {},
    close: () => {},
    send: () => {},
  }
  return {
    wsManager: {
      channel: () => channel,
      close: () => {},
      closeAll: () => {},
    },
  }
})

const listMessagesMock = vi.hoisted(() => vi.fn(async () => []))
const getMessageMock = vi.hoisted(() => vi.fn())
const listChatroomApprovalsMock = vi.hoisted(() => vi.fn(async () => []))
const getActiveActivationMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  listMessages: listMessagesMock,
  getMessage: getMessageMock,
  listChatroomApprovals: listChatroomApprovalsMock,
}))

vi.mock('@slices/activities', async (importOriginal) => ({
  ...(await importOriginal<typeof ActivitiesSlice>()),
  getActiveActivation: getActiveActivationMock,
}))

import {
  useChatroomSocket,
  AGENT_THINKING_TIMEOUT_MS,
} from '../composables/useChatroomSocket'
import { useConversationStore } from '../stores/conversation'

const ROOM = 'cr_1'
const AGENT = 'agent_1'

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

function emitDegraded(degraded: boolean): void {
  for (const h of [...degradedHandlers]) h(degraded)
}

/** Cross an agent.token flush boundary.
 *
 *  Tokens are buffered and written to the store at most once per 120ms window
 *  (F-15), so a case that asserts stream state after emitting tokens has to let
 *  the window close first. Named rather than inlined so the reason survives:
 *  a bare `advanceTimersByTime(120)` next to a stream assertion reads like a
 *  race workaround, which is the opposite of what it is. */
function flushTokenWindow(): void {
  vi.advanceTimersByTime(120)
}

function mountSocket(): {
  wrapper: VueWrapper
  store: ReturnType<typeof useConversationStore>
  qc: QueryClient
} {
  const pinia = createPinia()
  setActivePinia(pinia)
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const Host = defineComponent({
    setup() {
      useChatroomSocket(ROOM)
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [pinia, [VueQueryPlugin, { queryClient: qc }]] },
  })
  return { wrapper, store: useConversationStore(), qc }
}

/** Simulate useChatroomMessages' initial fetch having already populated the
 *  cache, so the QueryCache subscription seeds a cursor before any WS event
 *  fires — mirrors production, where both composables mount together. */
function seedCursor(qc: QueryClient, messageId: string, createdAt = '2024-01-01T00:00:00.000Z'): void {
  qc.setQueryData(['conversation', 'messages', ROOM], [
    { id: messageId, created_at: createdAt, sender_type: 'user', sender_id: 'u1' },
  ])
}

describe('useChatroomSocket agent streaming', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    subscribedHandlers.length = 0
    statusHandlers.length = 0
    degradedHandlers.length = 0
    listMessagesMock.mockClear()
    getMessageMock.mockReset()
    listChatroomApprovalsMock.mockReset()
    listChatroomApprovalsMock.mockResolvedValue([])
    getActiveActivationMock.mockReset()
    getActiveActivationMock.mockResolvedValue(null)
    vi.useFakeTimers()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.useRealTimers()
  })

  it('accumulates agent.token deltas in order', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'Hel', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'lo ', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'world', agent_id: AGENT })
    flushTokenWindow()
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('Hello world')
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
  })

  it('clears the streaming draft on agent.finished', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'partial reply', agent_id: AGENT })
    emit({ type: 'agent.finished', agent_id: AGENT })
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBeFalsy()
    // BUG-1 fix: draft cleared unconditionally on agent.finished — no ghost bubble.
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
  })

  it('surfaces the error kind and drops the draft on agent.finished{error}', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'doomed', agent_id: AGENT })
    emit({ type: 'agent.finished', error: 'provider_error', agent_id: AGENT })
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBeFalsy()
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentError[ROOM]).toBe('provider_error')
  })

  it('resets stale error state when a new turn starts', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.finished', error: 'provider_error', agent_id: AGENT })
    emit({ type: 'agent.thinking', agent_id: AGENT })
    expect(mounted.store.agentError[ROOM]).toBeNull()
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
  })

  it('pins the error kind to the agent for the sidebar badge', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.finished', error: 'rate_limited', agent_id: AGENT })
    // Per-agent badge state outlives the transient room-level toast trigger.
    expect(mounted.store.agentErrors[ROOM]?.[AGENT]).toBe('rate_limited')
  })

  it('clears the agent badge when the agent next thinks', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.finished', error: 'rate_limited', agent_id: AGENT })
    emit({ type: 'agent.thinking', agent_id: AGENT })
    expect(mounted.store.agentErrors[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('clears the agent badge once the agent posts a reply', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.finished', error: 'key_group_scope', agent_id: AGENT })
    emit({ type: 'message.created', message_id: 'm_agent', sender_type: 'agent', sender_id: AGENT })
    expect(mounted.store.agentErrors[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('clears a stale badge when the recovery reply arrives via delta-replay', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    // Seed a cursor (mirrors useChatroomMessages' initial fetch), then fail
    // the agent so the badge is lit.
    seedCursor(mounted.qc, 'm_seed')
    await flushPromises()
    emit({ type: 'agent.finished', error: 'rate_limited', agent_id: AGENT })
    expect(mounted.store.agentErrors[ROOM]?.[AGENT]).toBe('rate_limited')
    // On reconnect the agent's recovery reply is recovered via REST delta, not
    // the live message.created handler — the badge must still clear.
    listMessagesMock.mockResolvedValueOnce([
      { id: 'm_reply', sender_type: 'agent', sender_id: AGENT },
    ] as unknown as Awaited<ReturnType<typeof listMessagesMock>>)
    for (const h of [...statusHandlers]) h(true)
    await flushPromises()
    expect(mounted.store.agentErrors[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('times out a wedged turn and reports a timeout error', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'stuck', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS)
    expect(mounted.store.isAnyAgentThinking(ROOM)).toBe(false)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('re-arms the timeout on every agent.token', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    emit({ type: 'agent.token', text: 'still alive', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
    vi.advanceTimersByTime(1)
    expect(mounted.store.isAnyAgentThinking(ROOM)).toBe(false)
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('re-arms the timeout on agent.warning (F-15)', () => {
    // The engine already emits this mid-turn (R13.19) and the client used to
    // drop it at `default:` — a frame that proves the turn is alive was being
    // spent on nothing while the watchdog counted down toward a false timeout.
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    emit({ type: 'agent.warning', agent_id: AGENT, kind: 'skills_unavailable', dropped: 2 })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
    vi.advanceTimersByTime(1)
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('re-arms the timeout on agent.progress (F-15)', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    emit({ type: 'agent.progress', agent_id: AGENT, phase: 'history' })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1)
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
    vi.advanceTimersByTime(1)
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('never times out an assembly that keeps reporting progress', () => {
    // The whole of F-15: a compact-mode turn can spend minutes folding history
    // behind a 300s lock before the first token. Four beacons under the window
    // must carry it through with no error and no lost spinner.
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    for (const phase of ['context', 'skills', 'workspace', 'compacting']) {
      vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS - 1_000)
      emit({ type: 'agent.progress', agent_id: AGENT, phase })
    }
    emit({ type: 'agent.token', text: 'at last', agent_id: AGENT })
    flushTokenWindow()
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
    expect(mounted.store.agentThinking[ROOM]?.has(AGENT)).toBe(true)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('at last')
  })

  it('resets the streaming draft at a tool-round boundary (F-40)', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'let me check', agent_id: AGENT })
    emit({ type: 'agent.progress', agent_id: AGENT, phase: 'tool_round' })
    emit({ type: 'agent.token', text: 'the answer', agent_id: AGENT })
    flushTokenWindow()
    // What agent.finished replaces this with is the persisted reply, which is
    // the final round's text alone — so the two now match and nothing flashes.
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('the answer')
  })

  it('leaves the draft alone on a non-boundary progress phase', () => {
    // Only `tool_round` means "superseded". An assembly beacon arriving while
    // an earlier agent still holds a draft must not wipe it.
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'partial', agent_id: AGENT })
    emit({ type: 'agent.progress', agent_id: AGENT, phase: 'history' })
    flushTokenWindow()
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('partial')
  })

  it('resets only the emitting agents draft at a boundary', () => {
    const OTHER = 'agent_2'
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'mine', agent_id: AGENT })
    emit({ type: 'agent.thinking', agent_id: OTHER })
    emit({ type: 'agent.token', text: 'theirs', agent_id: OTHER })
    emit({ type: 'agent.progress', agent_id: AGENT, phase: 'tool_round' })
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentStreams[ROOM]?.[OTHER]).toBe('theirs')
  })

  it('clears only the thinking agents drafts when it fires', () => {
    // Defensive: one shared timer covers the room, so a fire means nobody spoke
    // for the whole window. Scoping the clear to the agents actually marked
    // thinking keeps a wedged agent from taking a neighbour's draft with it.
    const OTHER = 'agent_2'
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'stuck', agent_id: AGENT })
    // No agent.thinking for OTHER — it holds a draft but is not marked running.
    emit({ type: 'agent.token', text: 'neighbour', agent_id: OTHER })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentStreams[ROOM]?.[OTHER]).toBe('neighbour')
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('still clears the room draft when nothing is marked thinking', () => {
    // No per-agent set to scope to, but the watchdog has still declared the
    // room's turn dead — leaving the draft would strand it with no event left
    // to clear it.
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.token', text: 'orphan', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentError[ROOM]).toBe('timeout')
  })

  it('does not time out a turn that finished cleanly', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.finished', agent_id: AGENT })
    vi.advanceTimersByTime(AGENT_THINKING_TIMEOUT_MS * 2)
    expect(mounted.store.agentError[ROOM]).toBeFalsy()
  })

  it('does not clear agent stream on user message.created (BUG-4)', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'in progress', agent_id: AGENT })
    emit({ type: 'message.created', message_id: 'm_user', sender_type: 'user', sender_id: 'u1' })
    flushTokenWindow()
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('in progress')
  })

  it('syncs room activation state from started and ended events', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()

    emit({
      type: 'activity.activation.started',
      activation_id: 'activation_1',
      activity_type_id: 'type_1',
      started_by: 'user_1',
    })
    expect(activities.getActivation(ROOM)).toMatchObject({
      id: 'activation_1',
      activityTypeId: 'type_1',
    })

    emit({ type: 'activity.activation.ended', activation_id: 'activation_1' })
    expect(activities.getActivation(ROOM)).toBeNull()
  })

  it('routes a proposal broadcast into the store as counts', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    activities.setRound(ROOM, {
      activationId: 'activation_1',
      proposals: [
        {
          id: 'p_1',
          chatroom_id: ROOM,
          activation_id: 'activation_1',
          activity_type_id: 'type_1',
          member_group_id: 'g1',
          proposer_user_id: 'u1',
          payload: { answer: 'ours' },
          status: 'open',
          required_approvals: 2,
          approvals: 1,
          rejections: 0,
          undecided: 2,
          voter_count: 3,
          votes: [],
          created_at: null,
          expires_at: null,
          resolved_at: null,
          submission_id: null,
        },
      ],
      eligibleGroups: [{ id: 'g1', name: 'Group A' }],
    })

    emit({
      type: 'activity.proposal.voted',
      proposal_id: 'p_1',
      member_group_id: 'g1',
      status: 'open',
      approvals: 2,
      rejections: 0,
      undecided: 1,
      required_approvals: 2,
      voter_count: 3,
    })

    expect(activities.getProposalRoom(ROOM)?.proposals.p_1?.approvals).toBe(2)
  })

  it('drops every proposal card when the round ends (AC-9)', () => {
    // Ending a round expires its open proposals server-side, and the end
    // broadcast is the only announcement of that (FU-8) — so the cards go with
    // the activation rather than waiting for an event that never comes.
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    activities.setRound(ROOM, {
      activationId: 'activation_1',
      proposals: [],
      eligibleGroups: [{ id: 'g1', name: 'Group A' }],
    })

    emit({ type: 'activity.activation.ended', activation_id: 'activation_1' })

    expect(activities.getProposalRoom(ROOM)).toBeUndefined()
  })

  it('hydrates the current activation after reconnecting', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    getActiveActivationMock.mockResolvedValue({
      id: 'activation_recovered',
      chatroom_id: ROOM,
      activity_type_id: 'type_recovered',
      started_by_user_id: 'user_1',
      status: 'active',
      created_at: '2026-07-13T00:00:00Z',
      ended_at: null,
    })

    for (const handler of [...statusHandlers]) handler(true)
    await flushPromises()

    expect(getActiveActivationMock).toHaveBeenCalledWith(ROOM)
    expect(activities.getActivation(ROOM)).toMatchObject({
      id: 'activation_recovered',
      activityTypeId: 'type_recovered',
    })
  })

  it('clears a stale activation when reconnect hydration finds none', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    activities.setActivation(ROOM, {
      id: 'activation_stale',
      activityTypeId: 'type_stale',
      startedByUserId: 'user_1',
    })
    getActiveActivationMock.mockResolvedValue(null)

    for (const handler of [...statusHandlers]) handler(true)
    await flushPromises()

    expect(activities.getActivation(ROOM)).toBeNull()
  })

  it('does not overwrite a newer activation event with a stale reconnect response', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    let resolveActivation!: (value: null) => void
    getActiveActivationMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveActivation = resolve
      }),
    )

    for (const handler of [...statusHandlers]) handler(true)
    emit({
      type: 'activity.activation.started',
      activation_id: 'activation_newer',
      activity_type_id: 'type_newer',
      started_by: 'user_1',
    })
    resolveActivation(null)
    await flushPromises()

    expect(activities.getActivation(ROOM)).toMatchObject({
      id: 'activation_newer',
      activityTypeId: 'type_newer',
    })
  })

  it('does not overwrite a local activation transition with a stale reconnect response', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const activities = useActivitiesStore()
    activities.setActivation(ROOM, {
      id: 'activation_old',
      activityTypeId: 'type_old',
      startedByUserId: 'user_1',
    })
    let resolveActivation!: (value: {
      id: string
      chatroom_id: string
      activity_type_id: string
      started_by_user_id: string
      status: string
      created_at: string
      ended_at: null
    }) => void
    getActiveActivationMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveActivation = resolve
      }),
    )

    for (const handler of [...statusHandlers]) handler(true)
    activities.clearActivation(ROOM, 'activation_old')
    resolveActivation({
      id: 'activation_old',
      chatroom_id: ROOM,
      activity_type_id: 'type_old',
      started_by_user_id: 'user_1',
      status: 'active',
      created_at: '2026-07-13T00:00:00Z',
      ended_at: null,
    })
    await flushPromises()

    expect(activities.getActivation(ROOM)).toBeNull()
  })

  it('does not restore activation state after socket cleanup', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    let resolveActivation!: (value: {
      id: string
      chatroom_id: string
      activity_type_id: string
      started_by_user_id: string
      status: string
      created_at: string
      ended_at: null
    }) => void
    getActiveActivationMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveActivation = resolve
      }),
    )

    for (const handler of [...statusHandlers]) handler(true)
    wrapper.unmount()
    resolveActivation({
      id: 'activation_late',
      chatroom_id: ROOM,
      activity_type_id: 'type_late',
      started_by_user_id: 'user_1',
      status: 'active',
      created_at: '2026-07-13T00:00:00Z',
      ended_at: null,
    })
    await flushPromises()

    expect(useActivitiesStore().getActivation(ROOM)).toBeUndefined()
  })

  it('clears agent stream on agent message.created', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    seedCursor(mounted.qc, 'm_seed')
    await flushPromises()
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'done', agent_id: AGENT })
    listMessagesMock.mockResolvedValueOnce([
      { id: 'm_agent', created_at: '2024-01-01T00:00:01.000Z', sender_type: 'agent', sender_id: AGENT },
    ] as unknown as Awaited<ReturnType<typeof listMessagesMock>>)
    emit({ type: 'message.created', message_id: 'm_agent', sender_type: 'agent', sender_id: AGENT })
    await flushPromises()
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('polls the message delta every 10s while the socket is degraded (§7.1)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    // Seed a cursor so replayDelta has a `since` to fetch from.
    seedCursor(mounted.qc, 'm_seed')
    await flushPromises()
    listMessagesMock.mockClear()

    emitDegraded(true)
    expect(listMessagesMock).not.toHaveBeenCalled()

    vi.advanceTimersByTime(10_000)
    expect(listMessagesMock).toHaveBeenCalledWith(ROOM, { since: 'm_seed' })

    vi.advanceTimersByTime(10_000)
    expect(listMessagesMock).toHaveBeenCalledTimes(2)

    // Recovery stops the poll.
    emitDegraded(false)
    listMessagesMock.mockClear()
    vi.advanceTimersByTime(30_000)
    expect(listMessagesMock).not.toHaveBeenCalled()
  })

  it('does not resurrect a message deleted while its create-delta is in flight (B2)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    seedCursor(mounted.qc, 'm_seed')
    await flushPromises()

    let resolveDelta!: (messages: unknown) => void
    listMessagesMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveDelta = resolve
      }),
    )
    // message.created kicks off the delta fetch, but it does not resolve yet.
    emit({ type: 'message.created', message_id: 'm_new', sender_type: 'user', sender_id: 'u1' })

    // The delete WS frame for the same message beats the HTTP response back.
    emit({ type: 'message.deleted', message_id: 'm_new' })

    resolveDelta([
      { id: 'm_new', created_at: '2024-01-01T00:00:01.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{ id: string }>
    expect(cache.some((m) => m.id === 'm_new')).toBe(false)
  })

  it('reconciles a message deleted while the socket was down (F-11)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [
      { id: 'm_1', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
      { id: 'm_2', created_at: '2024-01-01T00:00:01.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    statusHandlers.forEach((h) => h(false))
    // The deleted-while-down message (m_2) is simply absent from the page —
    // no `message.deleted` frame is needed for this path to work.
    listMessagesMock.mockResolvedValueOnce([
      { id: 'm_1', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{ id: string }>
    expect(cache.some((m) => m.id === 'm_2')).toBe(false)
    expect(cache.some((m) => m.id === 'm_1')).toBe(true)
  })

  it('reconciles a message edited while the socket was down (F-11, AC-2)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [
      {
        id: 'm_1',
        created_at: '2024-01-01T00:00:00.000Z',
        sender_type: 'user',
        sender_id: 'u1',
        content_md: 'original',
        version: 1,
      },
    ])
    // The edited-while-down message (m_1) comes back with its new content and
    // version — no live `message.updated` frame is needed for this path.
    listMessagesMock.mockResolvedValueOnce([
      {
        id: 'm_1',
        created_at: '2024-01-01T00:00:00.000Z',
        sender_type: 'user',
        sender_id: 'u1',
        content_md: 'edited while disconnected',
        version: 2,
      },
    ])
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{
      id: string
      content_md: string
      version: number
    }>
    expect(cache.find((m) => m.id === 'm_1')).toMatchObject({
      content_md: 'edited while disconnected',
      version: 2,
    })
  })

  it('tracks the newest message as the cursor after a connect reconcile, regardless of page order (F-11)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    // The backend returns the plain (no since/before) page newest-first —
    // the last element here is deliberately the OLDEST, to catch a
    // regression that (wrongly) reads the cursor off the last array index.
    listMessagesMock.mockResolvedValueOnce([
      { id: 'm_newest', created_at: '2024-01-01T00:00:02.000Z', sender_type: 'user', sender_id: 'u1' },
      { id: 'm_oldest', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    // A subsequent since-delta (e.g. the degraded poll) must anchor on the
    // truly newest message, not whichever one happened to be last in the page.
    listMessagesMock.mockClear()
    emitDegraded(true)
    vi.advanceTimersByTime(10_000)
    expect(listMessagesMock).toHaveBeenCalledWith(ROOM, { since: 'm_newest' })
  })

  it('does not drop messages older than the fetched window on connect (F-11)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [
      { id: 'm_ancient', created_at: '2023-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
      { id: 'm_1', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    // The connect fetch's page only reaches back to m_1 — m_ancient is outside
    // its window and must not be treated as deleted (Q-3's boundary).
    listMessagesMock.mockResolvedValueOnce([
      { id: 'm_1', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{ id: string }>
    expect(cache.some((m) => m.id === 'm_ancient')).toBe(true)
  })

  it('overlapping connect reconciliations cannot apply stale data (F-11)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [])

    let resolveFirst!: (m: unknown) => void
    let resolveSecond!: (m: unknown) => void
    listMessagesMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))

    statusHandlers.forEach((h) => h(true))
    statusHandlers.forEach((h) => h(false))
    statusHandlers.forEach((h) => h(true))

    // The second (newer) connect's fetch resolves first with the authoritative
    // page; the first (stale) one resolves after and must be dropped.
    resolveSecond([
      { id: 'm_new', created_at: '2024-01-02T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    await flushPromises()
    resolveFirst([
      { id: 'm_stale', created_at: '2024-01-01T00:00:00.000Z', sender_type: 'user', sender_id: 'u1' },
    ])
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{ id: string }>
    expect(cache.some((m) => m.id === 'm_stale')).toBe(false)
    expect(cache.some((m) => m.id === 'm_new')).toBe(true)
  })

  function approval(over: Partial<ApprovalWithVotes> = {}): ApprovalWithVotes {
    return {
      id: 'ap_1',
      workflow_run_id: 'run_1',
      mode: 'single',
      leader_agent_id: AGENT,
      approver_agent_ids: [AGENT],
      timeout_seconds: 300,
      state: 'pending',
      started_at: '2024-01-01T00:00:00.000Z',
      ended_at: null,
      votes: [],
      ...over,
    }
  }

  it('discovers an approval raised while disconnected (F-13)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const orchStore = useOrchestrationStore()
    expect(orchStore.getApprovalsForRoom(ROOM)).toHaveLength(0)

    listChatroomApprovalsMock.mockResolvedValueOnce([approval({ id: 'ap_missed' })])
    statusHandlers.forEach((h) => h(true))
    await flushPromises()

    expect(orchStore.getApprovalsForRoom(ROOM).map((a) => a.id)).toContain('ap_missed')
  })

  it('does not resurrect a resolved approval from a stale discovery response (F-13)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const orchStore = useOrchestrationStore()

    let resolveFirst!: (v: ApprovalWithVotes[]) => void
    let resolveSecond!: (v: ApprovalWithVotes[]) => void
    listChatroomApprovalsMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))

    statusHandlers.forEach((h) => h(true))
    statusHandlers.forEach((h) => h(false))
    statusHandlers.forEach((h) => h(true))

    // The second (newer) connect's discovery resolves first, finding nothing
    // -- the gate is already resolved and no longer relevant.
    resolveSecond([])
    await flushPromises()
    // The first (stale) discovery resolves after, still naming the old gate.
    // It must not resurrect a card the newer fetch correctly omitted.
    resolveFirst([approval({ id: 'ap_stale' })])
    await flushPromises()

    expect(orchStore.getApprovalsForRoom(ROOM).some((a) => a.id === 'ap_stale')).toBe(false)
  })

  it('applies only the newest message.updated when two refetches resolve out of order (F-19)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [
      { id: 'm_1', version: 1, content_md: 'original', created_at: '2024-01-01T00:00:00.000Z' },
    ])

    let resolveFirst!: (m: unknown) => void
    let resolveSecond!: (m: unknown) => void
    getMessageMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))

    emit({ type: 'message.updated', message_id: 'm_1' })
    emit({ type: 'message.updated', message_id: 'm_1' })

    // The second (newer) refetch resolves first; the first (stale) one resolves last.
    resolveSecond({ id: 'm_1', version: 3, content_md: 'edit 2' })
    await flushPromises()
    resolveFirst({ id: 'm_1', version: 2, content_md: 'edit 1' })
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{
      id: string
      version: number
      content_md: string
    }>
    expect(cache.find((m) => m.id === 'm_1')).toMatchObject({ version: 3, content_md: 'edit 2' })
  })

  it('does not leave a stale version in the cache after out-of-order edits (F-19)', async () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    mounted.qc.setQueryData(['conversation', 'messages', ROOM], [
      { id: 'm_1', version: 1, content_md: 'original', created_at: '2024-01-01T00:00:00.000Z' },
    ])

    let resolveFirst!: (m: unknown) => void
    let resolveSecond!: (m: unknown) => void
    getMessageMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve }))

    emit({ type: 'message.updated', message_id: 'm_1' })
    emit({ type: 'message.updated', message_id: 'm_1' })
    resolveSecond({ id: 'm_1', version: 3, content_md: 'edit 2' })
    await flushPromises()
    resolveFirst({ id: 'm_1', version: 2, content_md: 'edit 1' })
    await flushPromises()

    const cache = mounted.qc.getQueryData(['conversation', 'messages', ROOM]) as Array<{
      id: string
      version: number
    }>
    expect(cache.find((m) => m.id === 'm_1')?.version).not.toBe(2)
  })
})

// T-6 of docs/tasks/2026-08-19-chatroom-scroll-and-composer (F-15).
//
// Every agent.token frame used to reach the store, which reassigns agentStreams
// immutably -- so each token cost a render, a renderMarkdown (markdown-it plus
// DOMPurify) and a full v-html subtree replacement. The memo in useAgentStreams
// keys on text equality and therefore cannot hit while the text is growing,
// which is the only time it would matter.
//
// The forced flushes are the load-bearing half: without them the tail of a
// reply would be written AFTER the draft is cleared, which is a ghost bubble --
// a worse defect than the one being fixed.
describe('useChatroomSocket agent.token throttling (F-15)', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    subscribedHandlers.length = 0
    statusHandlers.length = 0
    degradedHandlers.length = 0
    listMessagesMock.mockClear()
    getMessageMock.mockReset()
    listChatroomApprovalsMock.mockReset()
    listChatroomApprovalsMock.mockResolvedValue([])
    getActiveActivationMock.mockReset()
    getActiveActivationMock.mockResolvedValue(null)
    vi.useFakeTimers()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.useRealTimers()
  })

  it('collapses a burst inside one window into a single store write', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const append = vi.spyOn(mounted.store, 'appendAgentToken')

    emit({ type: 'agent.thinking', agent_id: AGENT })
    for (let i = 0; i < 30; i++) {
      emit({ type: 'agent.token', text: `t${i} `, agent_id: AGENT })
    }
    expect(append).not.toHaveBeenCalled()

    vi.advanceTimersByTime(120)

    expect(append).toHaveBeenCalledTimes(1)
    const expected = Array.from({ length: 30 }, (_, i) => `t${i} `).join('')
    expect(append).toHaveBeenCalledWith(ROOM, AGENT, expected)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe(expected)
  })

  it('keeps each agent in its own buffer', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper

    emit({ type: 'agent.token', text: 'alpha', agent_id: 'agent_a' })
    emit({ type: 'agent.token', text: 'beta', agent_id: 'agent_b' })
    emit({ type: 'agent.token', text: '-two', agent_id: 'agent_a' })
    vi.advanceTimersByTime(120)

    expect(mounted.store.agentStreams[ROOM]?.['agent_a']).toBe('alpha-two')
    expect(mounted.store.agentStreams[ROOM]?.['agent_b']).toBe('beta')
  })

  it('opens a fresh window per burst rather than one repeating timer', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const append = vi.spyOn(mounted.store, 'appendAgentToken')

    emit({ type: 'agent.token', text: 'a', agent_id: AGENT })
    vi.advanceTimersByTime(120)
    emit({ type: 'agent.token', text: 'b', agent_id: AGENT })
    vi.advanceTimersByTime(120)

    expect(append).toHaveBeenCalledTimes(2)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('ab')
  })

  it('flushes before agent.finished clears the draft, leaving no ghost bubble', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    const append = vi.spyOn(mounted.store, 'appendAgentToken')

    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'the tail', agent_id: AGENT })
    emit({ type: 'agent.finished', agent_id: AGENT })

    expect(append).toHaveBeenCalledTimes(1)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()

    // The window would have elapsed here. Nothing may arrive after the clear.
    vi.advanceTimersByTime(500)
    expect(append).toHaveBeenCalledTimes(1)
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('flushes on the agent.finished error path too', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper

    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'doomed', agent_id: AGENT })
    emit({ type: 'agent.finished', error: 'provider_error', agent_id: AGENT })
    vi.advanceTimersByTime(500)

    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
    expect(mounted.store.agentError[ROOM]).toBe('provider_error')
  })

  it('does not let a superseded tool round resurrect itself', () => {
    // agent.progress{tool_round} clears the draft because only the final round
    // is persisted (F-40). A token buffered before that clear must not land
    // after it, or the superseded round reappears on top of the new one.
    const mounted = mountSocket()
    wrapper = mounted.wrapper

    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'round one output', agent_id: AGENT })
    emit({ type: 'agent.progress', phase: 'tool_round', agent_id: AGENT })
    vi.advanceTimersByTime(500)

    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeFalsy()
  })

  it('does not let the previous turn bleed into a new one', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper

    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'old turn', agent_id: AGENT })
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'new turn', agent_id: AGENT })
    vi.advanceTimersByTime(120)

    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBe('new turn')
  })

  it('writes nothing after unmount', () => {
    const mounted = mountSocket()
    const append = vi.spyOn(mounted.store, 'appendAgentToken')

    emit({ type: 'agent.token', text: 'mid window', agent_id: AGENT })
    mounted.wrapper.unmount()
    wrapper = null
    const callsAtUnmount = append.mock.calls.length

    vi.advanceTimersByTime(500)

    expect(append).toHaveBeenCalledTimes(callsAtUnmount)
  })
})

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

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []
const degradedHandlers: Array<(degraded: boolean) => void> = []

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
const getActiveActivationMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  listMessages: listMessagesMock,
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
})

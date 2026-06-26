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
vi.mock('../api', () => ({
  listMessages: listMessagesMock,
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
  return { wrapper, store: useConversationStore() }
}

describe('useChatroomSocket agent streaming', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    subscribedHandlers.length = 0
    statusHandlers.length = 0
    degradedHandlers.length = 0
    listMessagesMock.mockClear()
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
    // Seed a cursor, then fail the agent so the badge is lit.
    emit({ type: 'message.created', message_id: 'm_seed', sender_type: 'user', sender_id: 'u1' })
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

  it('clears agent stream on agent message.created', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    emit({ type: 'agent.thinking', agent_id: AGENT })
    emit({ type: 'agent.token', text: 'done', agent_id: AGENT })
    emit({ type: 'message.created', message_id: 'm_agent', sender_type: 'agent', sender_id: AGENT })
    expect(mounted.store.agentStreams[ROOM]?.[AGENT]).toBeUndefined()
  })

  it('polls the message delta every 10s while the socket is degraded (§7.1)', () => {
    const mounted = mountSocket()
    wrapper = mounted.wrapper
    // Seed a cursor so replayDelta has a `since` to fetch from.
    emit({ type: 'message.created', message_id: 'm_seed', sender_type: 'user', sender_id: 'u1' })
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
})

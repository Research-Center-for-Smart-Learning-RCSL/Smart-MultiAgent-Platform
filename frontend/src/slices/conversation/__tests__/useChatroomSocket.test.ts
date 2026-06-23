// Streaming/error/timeout behaviour of the chatroom WS subscriber.
//
// The transport layer is mocked; events are injected by invoking the
// wildcard handler the composable registers via channel.subscribe('*', …).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'

import type { ChannelEvent } from '@shared/transport'

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []

vi.mock('@shared/transport', () => {
  const channel = {
    subscribe: (_name: string, handler: (ev: ChannelEvent) => void) => {
      subscribedHandlers.push(handler)
      return () => {}
    },
    onStatus: () => () => {},
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

vi.mock('../api', () => ({
  listMessages: vi.fn(async () => []),
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
})

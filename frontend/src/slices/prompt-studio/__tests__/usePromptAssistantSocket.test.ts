// The prompt-assistant WS subscriber reduces prompt.token / prompt.finished /
// prompt.error events into a reactive message list + streaming buffer. The
// transport layer is mocked; events are injected through the wildcard handler
// the composable registers (mirrors useGraphragSocket.test.ts).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'

import type { ChannelEvent } from '@shared/transport'

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []
let connectCalls = 0

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
    connect: () => {
      connectCalls += 1
    },
    disconnect: () => {},
    close: () => {},
  }
  return { wsManager: { channel: () => channel, close: () => {} } }
})

import { usePromptAssistantSocket } from '../composables/usePromptAssistantSocket'

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

afterEach(() => {
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  connectCalls = 0
  vi.clearAllMocks()
})

function mountSocket() {
  const sessionId = ref<string | null>(null)
  let api!: ReturnType<typeof usePromptAssistantSocket>
  const Host = defineComponent({
    setup() {
      api = usePromptAssistantSocket(sessionId)
      return () => null
    },
  })
  mount(Host)
  return { api, sessionId }
}

describe('usePromptAssistantSocket', () => {
  it('does not connect until a session id is set', async () => {
    const { sessionId } = mountSocket()
    expect(connectCalls).toBe(0)
    sessionId.value = 'sess_1'
    await nextTick()
    expect(connectCalls).toBe(1)
  })

  it('accumulates streamed tokens then commits the finished reply', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'Hello ' })
    emit({ type: 'prompt.token', text: 'world' })
    expect(api.streaming.value).toBe(true)
    expect(api.streamingText.value).toBe('Hello world')

    emit({ type: 'prompt.finished' })
    expect(api.streaming.value).toBe(false)
    expect(api.streamingText.value).toBe('')
    expect(api.messages.value).toEqual([{ role: 'assistant', content: 'Hello world' }])
  })

  it('surfaces the error code and clears the streaming buffer on prompt.error', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'partial' })
    emit({ type: 'prompt.error', code: 'prompt-studio/quota-exceeded' })

    expect(api.errorCode.value).toBe('prompt-studio/quota-exceeded')
    expect(api.streaming.value).toBe(false)
    expect(api.streamingText.value).toBe('')
  })

  it('resets state when the session id changes', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()
    api.pushUserMessage('hi')
    expect(api.messages.value.length).toBe(1)

    sessionId.value = 'sess_2'
    await nextTick()
    expect(api.messages.value.length).toBe(0)
    expect(connectCalls).toBe(2)
  })
})

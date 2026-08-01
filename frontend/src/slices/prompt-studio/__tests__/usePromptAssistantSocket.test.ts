// The prompt-assistant WS subscriber reduces prompt.token / prompt.finished /
// prompt.error events into a reactive message list + streaming buffer. The
// transport layer is mocked; events are injected through the wildcard handler
// the composable registers (mirrors useGraphragSocket.test.ts). The recovery
// fetch (F-13 fix) is exercised by driving statusHandlers directly, mirroring
// useChatroomSocket.test.ts's connect-time reconcile tests.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'

import type { ChannelEvent } from '@shared/transport'
import { ApiError } from '@shared/api-client'

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

const getSessionMock = vi.hoisted(() => vi.fn())
vi.mock('../api', () => ({
  promptStudioApi: { getSession: getSessionMock },
}))

import { usePromptAssistantSocket, ASSISTANT_STREAM_TIMEOUT_MS } from '../composables/usePromptAssistantSocket'

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

function emitStatus(connected: boolean): void {
  for (const h of [...statusHandlers]) h(connected)
}

function apiError(status: number): ApiError {
  return new ApiError(
    {} as never,
    { url: '', ok: false, status, statusText: '', body: {} },
    'request failed',
  )
}

beforeEach(() => {
  getSessionMock.mockReset()
  getSessionMock.mockResolvedValue({ session_id: 'sess_1', messages: [] })
})

afterEach(() => {
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  connectCalls = 0
  vi.clearAllMocks()
  vi.useRealTimers()
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

  it('clears streaming when the socket reconnects mid-turn', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'partial' })
    expect(api.streaming.value).toBe(true)

    emitStatus(false) // socket drops
    emitStatus(true) // reconnects; the terminal frame was lost
    await flushPromises()

    expect(api.streaming.value).toBe(false)
  })

  it('refetches the session on every connect, including the first', async () => {
    const { sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emitStatus(true)
    await flushPromises()

    expect(getSessionMock).toHaveBeenCalledWith('sess_1')
    expect(getSessionMock).toHaveBeenCalledTimes(1)
  })

  it('reconciles a reply that arrived while disconnected', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    api.pushUserMessage('draft me a prompt')
    getSessionMock.mockResolvedValueOnce({
      session_id: 'sess_1',
      messages: [
        { role: 'user', content: 'draft me a prompt', error: false },
        { role: 'assistant', content: 'here you go', error: false },
      ],
    })

    emitStatus(true)
    await flushPromises()

    expect(api.messages.value).toEqual([
      { role: 'user', content: 'draft me a prompt' },
      { role: 'assistant', content: 'here you go' },
    ])
  })

  it('discards a stale refetch that resolves after a newer one (AC-7)', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    let resolveFirst!: (v: unknown) => void
    let resolveSecond!: (v: unknown) => void
    const first = new Promise((resolve) => {
      resolveFirst = resolve
    })
    const second = new Promise((resolve) => {
      resolveSecond = resolve
    })
    getSessionMock.mockReturnValueOnce(first).mockReturnValueOnce(second)

    emitStatus(true) // reconnect #1 -> starts fetch #1 (never resolves yet)
    emitStatus(false)
    emitStatus(true) // reconnect #2 -> starts fetch #2, bumping the generation

    // The newer fetch resolves first with the true current state...
    resolveSecond({
      session_id: 'sess_1',
      messages: [{ role: 'assistant', content: 'fresh', error: false }],
    })
    await flushPromises()
    expect(api.messages.value).toEqual([{ role: 'assistant', content: 'fresh' }])

    // ...then the older, slower fetch resolves with data that was already
    // stale when it was requested. A flapping socket makes this ordering
    // routine, not exotic -- it must never overwrite the fresher result.
    resolveFirst({
      session_id: 'sess_1',
      messages: [{ role: 'assistant', content: 'stale', error: false }],
    })
    await flushPromises()
    expect(api.messages.value).toEqual([{ role: 'assistant', content: 'fresh' }])
  })

  it('keeps an optimistic turn the server has not caught up to yet', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    // The server list is shorter than the local one -- the optimistic push
    // raced ahead of its own POST completing server-side.
    getSessionMock.mockResolvedValueOnce({ session_id: 'sess_1', messages: [] })
    api.pushUserMessage('brand new turn')

    emitStatus(true)
    await flushPromises()

    expect(api.messages.value).toEqual([{ role: 'user', content: 'brand new turn' }])
  })

  it('tolerates a 404 from the session refetch', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'partial' })
    getSessionMock.mockRejectedValueOnce(apiError(404))

    emitStatus(true)
    await flushPromises()

    expect(api.sessionExpired.value).toBe(true)
    expect(api.streaming.value).toBe(false)
  })

  it('clears streaming after the watchdog timeout with no terminal frame', async () => {
    vi.useFakeTimers()
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'stuck' })
    vi.advanceTimersByTime(ASSISTANT_STREAM_TIMEOUT_MS)

    expect(api.streaming.value).toBe(false)
    expect(api.errorCode.value).toBe('prompt-studio/timeout')
  })

  it('clears a stale watchdog timeout once a reconnect recovers the real outcome', async () => {
    vi.useFakeTimers()
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'stuck' })
    vi.advanceTimersByTime(ASSISTANT_STREAM_TIMEOUT_MS)
    expect(api.errorCode.value).toBe('prompt-studio/timeout')

    // The turn actually succeeded server-side; the terminal frame was just
    // never delivered. A later reconnect's refetch recovers it -- the stale
    // "took too long" guess must not linger next to the real reply.
    getSessionMock.mockResolvedValueOnce({
      session_id: 'sess_1',
      messages: [{ role: 'assistant', content: 'the real reply', error: false }],
    })
    emitStatus(false)
    emitStatus(true)
    await flushPromises()

    expect(api.errorCode.value).toBeNull()
    expect(api.messages.value).toEqual([{ role: 'assistant', content: 'the real reply' }])
  })

  it('does not clear an unrelated quota-exceeded error on reconnect', async () => {
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.error', code: 'prompt-studio/quota-exceeded' })
    expect(api.errorCode.value).toBe('prompt-studio/quota-exceeded')

    getSessionMock.mockResolvedValueOnce({ session_id: 'sess_1', messages: [] })
    emitStatus(false)
    emitStatus(true)
    await flushPromises()

    // Quota state has nothing to do with socket/turn lifecycle -- only the
    // watchdog's own 'timeout' guess gets superseded by a refetch.
    expect(api.errorCode.value).toBe('prompt-studio/quota-exceeded')
  })

  it('re-arms the watchdog on each token', async () => {
    vi.useFakeTimers()
    const { api, sessionId } = mountSocket()
    sessionId.value = 'sess_1'
    await nextTick()

    emit({ type: 'prompt.token', text: 'a' })
    vi.advanceTimersByTime(ASSISTANT_STREAM_TIMEOUT_MS - 1)
    emit({ type: 'prompt.token', text: 'b' })
    vi.advanceTimersByTime(ASSISTANT_STREAM_TIMEOUT_MS - 1)

    expect(api.streaming.value).toBe(true)
    expect(api.errorCode.value).toBeNull()

    vi.advanceTimersByTime(1)
    expect(api.streaming.value).toBe(false)
    expect(api.errorCode.value).toBe('prompt-studio/timeout')
  })
})

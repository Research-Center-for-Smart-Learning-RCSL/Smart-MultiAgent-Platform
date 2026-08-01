import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

import { ApiError } from '@shared/api-client'
import { type ChannelEvent, wsManager } from '@shared/transport'

import { promptStudioApi } from '../api'

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
  /** True for a worker-persisted failure marker recovered via refetch (Q-5) -- never set on the live path. */
  error?: boolean
}

// Client-side watchdog for a wedged turn: if the terminal frame (prompt.finished /
// prompt.error) is lost mid-stream, `streaming` would otherwise stick forever and
// permanently disable the composer (SButton turns `loading` into `disabled`).
// Re-armed on every prompt.token, so the bound is inter-token silence, not total
// duration -- ASSISTANT_MAX_TOKENS already bounds total output. Transliterates
// useChatroomSocket.ts's AGENT_THINKING_TIMEOUT_MS pattern.
export const ASSISTANT_STREAM_TIMEOUT_MS = 120_000

/**
 * Subscribes to `/ws/prompt-assistant/{sessionId}` and reduces the worker's
 * `prompt.token` / `prompt.finished` / `prompt.error` events into a reactive
 * message list + streaming buffer. Sending is done over HTTP (the message POST
 * enqueues a turn); this composable only consumes the streamed reply.
 *
 * On every connect (including the first, not just reconnects -- the recovery
 * fetch closes the same handshake window a `postMessage` gate would, without
 * blocking a user who can't open a socket at all), stale in-flight state is
 * cleared and the session is refetched from the server, which is the durable
 * read side `prompt.token`/`prompt.finished`/`prompt.error` alone never had.
 */
export function usePromptAssistantSocket(sessionId: Ref<string | null>) {
  const messages = ref<AssistantMessage[]>([])
  const streamingText = ref('')
  const streaming = ref(false)
  const connected = ref(false)
  const errorCode = ref<string | null>(null)
  const sessionExpired = ref(false)

  let teardown: (() => void) | null = null

  // Bumped on every connect; a reconcile whose generation is stale when it
  // resolves is discarded so a flapping socket can't apply a slower earlier
  // fetch's data over a fresher one (mirrors useChatroomSocket.ts's
  // replayGeneration).
  let refetchGeneration = 0

  let watchdogTimer: ReturnType<typeof setTimeout> | null = null

  function clearWatchdog(): void {
    if (watchdogTimer !== null) {
      clearTimeout(watchdogTimer)
      watchdogTimer = null
    }
  }

  function armWatchdog(): void {
    clearWatchdog()
    watchdogTimer = setTimeout(() => {
      watchdogTimer = null
      streaming.value = false
      streamingText.value = ''
      errorCode.value = 'prompt-studio/timeout'
    }, ASSISTANT_STREAM_TIMEOUT_MS)
  }

  async function reconcile(id: string): Promise<void> {
    const generation = ++refetchGeneration
    try {
      const session = await promptStudioApi.getSession(id)
      if (generation !== refetchGeneration) return
      sessionExpired.value = false
      // The watchdog's guess (no terminal frame arrived) is superseded the
      // moment a refetch succeeds -- unlike a real prompt.error or the POST's
      // quota-exceeded rejection, 'timeout' is a purely local placeholder
      // with no server-side meaning to preserve, so only it is cleared here.
      if (errorCode.value === 'prompt-studio/timeout') errorCode.value = null
      const server: AssistantMessage[] = session.messages.map((m) => ({
        role: m.role as AssistantMessage['role'],
        content: m.content,
        ...(m.error ? { error: true as const } : {}),
      }))
      // Messages have no ids (backend/contexts/prompt_studio/domain/models.py),
      // so id-based dedup is unavailable. Treat the server list as authoritative
      // and replace wholesale, keeping only the local suffix the server hasn't
      // caught up to yet -- the optimistic user turn pushed just before a
      // refetch happened to race in ahead of its own POST completing.
      const extra = messages.value.length > server.length ? messages.value.slice(server.length) : []
      messages.value = [...server, ...extra]
    } catch (err) {
      if (generation !== refetchGeneration) return
      if (err instanceof ApiError && err.status === 404) {
        // Distinct from a transient error: the 2h TTL genuinely elapsed (a tab
        // left overnight). Not folded into errorCode, which the composer
        // treats as retryable.
        sessionExpired.value = true
        messages.value = []
        streamingText.value = ''
        if (errorCode.value === 'prompt-studio/timeout') errorCode.value = null
      }
      // Otherwise best-effort, matching resyncPresence/resyncActivation in
      // useChatroomSocket.ts: the next connect gets another chance.
    }
  }

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'prompt.token':
        streaming.value = true
        streamingText.value += String(ev.text ?? '')
        armWatchdog()
        break
      case 'prompt.finished': {
        clearWatchdog()
        const text = String(ev.text ?? streamingText.value)
        messages.value.push({ role: 'assistant', content: text })
        streamingText.value = ''
        streaming.value = false
        break
      }
      case 'prompt.error':
        clearWatchdog()
        errorCode.value = String(ev.code ?? 'prompt-studio/turn-failed')
        streamingText.value = ''
        streaming.value = false
        break
    }
  }

  function connect(id: string): void {
    const path = `/prompt-assistant/${id}`
    const channel = wsManager.channel(path)
    const unsubEvent = channel.subscribe('*', handleEvent)
    const unsubStatus = channel.onStatus((isConnected) => {
      connected.value = isConnected
      if (!isConnected) return
      // Clear stale in-flight state before refetching, on every connect --
      // the cheapest correct fix for the wedge on its own: once a reconnect
      // unconditionally resets these, a lost terminal frame can no longer
      // strand them (mirrors useChatroomSocket.ts's connect-time reset).
      clearWatchdog()
      streaming.value = false
      streamingText.value = ''
      void reconcile(id)
    })
    channel.connect()
    teardown = () => {
      unsubEvent()
      unsubStatus()
      wsManager.close(path)
      connected.value = false
    }
  }

  watch(
    sessionId,
    (id) => {
      teardown?.()
      teardown = null
      clearWatchdog()
      refetchGeneration += 1
      messages.value = []
      streamingText.value = ''
      streaming.value = false
      errorCode.value = null
      sessionExpired.value = false
      if (id) connect(id)
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    teardown?.()
    teardown = null
    clearWatchdog()
  })

  /** Optimistically record the user's turn before the reply streams back. */
  function pushUserMessage(content: string): void {
    messages.value.push({ role: 'user', content })
    errorCode.value = null
  }

  return {
    messages,
    streamingText,
    streaming,
    connected,
    errorCode,
    sessionExpired,
    pushUserMessage,
  }
}

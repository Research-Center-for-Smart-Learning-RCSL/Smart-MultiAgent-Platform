import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

import { type ChannelEvent, wsManager } from '@shared/transport'

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
}

/**
 * Subscribes to `/ws/prompt-assistant/{sessionId}` and reduces the worker's
 * `prompt.token` / `prompt.finished` / `prompt.error` events into a reactive
 * message list + streaming buffer. Sending is done over HTTP (the message POST
 * enqueues a turn); this composable only consumes the streamed reply.
 */
export function usePromptAssistantSocket(sessionId: Ref<string | null>) {
  const messages = ref<AssistantMessage[]>([])
  const streamingText = ref('')
  const streaming = ref(false)
  const connected = ref(false)
  const errorCode = ref<string | null>(null)

  let teardown: (() => void) | null = null

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'prompt.token':
        streaming.value = true
        streamingText.value += String(ev.text ?? '')
        break
      case 'prompt.finished': {
        const text = String(ev.text ?? streamingText.value)
        messages.value.push({ role: 'assistant', content: text })
        streamingText.value = ''
        streaming.value = false
        break
      }
      case 'prompt.error':
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
      messages.value = []
      streamingText.value = ''
      streaming.value = false
      errorCode.value = null
      if (id) connect(id)
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    teardown?.()
    teardown = null
  })

  /** Optimistically record the user's turn before the reply streams back. */
  function pushUserMessage(content: string): void {
    messages.value.push({ role: 'user', content })
    errorCode.value = null
  }

  return { messages, streamingText, streaming, connected, errorCode, pushUserMessage }
}

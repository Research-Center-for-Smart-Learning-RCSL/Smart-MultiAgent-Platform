// WS subscriber — the SOLE entry point for real-time updates (R24.21).
//
// Emits events into:
//   - TanStack Query (setQueryData / invalidateQueries) for message list
//   - The `useConversationStore` Pinia store for presence / ephemeral flags
//
// On reconnect (R13.20 / R24.23) we fetch `GET /messages?since=<last_id>`
// so the client recovers the delta the server did not replay.

import { useQueryClient } from '@tanstack/vue-query'
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { useOrchestrationStore } from '@slices/workflow'
import type { ApprovalWithVotes } from '@slices/workflow'
import { listMessages } from '../api'
import { useConversationStore } from '../stores/conversation'
import type { Message } from '../types'

// Client-side watchdog for a wedged turn: if the worker crashes mid-turn no
// `agent.finished` ever arrives, so without this the thinking spinner sticks
// forever. Re-armed on every `agent.token`, cleared on `agent.finished`.
export const AGENT_THINKING_TIMEOUT_MS = 120_000

export function useChatroomSocket(roomId: string) {
  const qc = useQueryClient()
  const store = useConversationStore()
  const orchStore = useOrchestrationStore()
  const connected = ref(false)
  const lastSeenMessageId = ref<string | null>(null)

  const channel = wsManager.channel(`/chatroom/${roomId}`)

  // Monotonic generation guard: each reconnect bumps this counter, so if the
  // socket flaps and two replays overlap, a slower earlier fetch cannot
  // resolve last and re-apply an older delta over fresher data (R24.23).
  let replayGeneration = 0

  async function replayDelta(): Promise<void> {
    if (!lastSeenMessageId.value) return
    const generation = ++replayGeneration
    try {
      const delta = await listMessages(roomId, { since: lastSeenMessageId.value })
      // A newer reconnect superseded this replay while it was in flight — drop
      // it so lastSeenMessageId cannot move backwards.
      if (generation !== replayGeneration) return
      for (const m of delta) applyMessageCreated(m)
    } catch {
      // Non-fatal — the user can refresh manually.
    }
  }

  function applyMessageCreated(m: Message): void {
    const key = ['conversation', 'messages', roomId]
    qc.setQueryData<Message[]>(key, (prev) => {
      if (!prev) return [m]
      if (prev.some((x) => x.id === m.id)) return prev
      return [...prev, m]
    })
    lastSeenMessageId.value = m.id
  }

  let thinkingTimer: ReturnType<typeof setTimeout> | null = null

  function clearThinkingTimeout(): void {
    if (thinkingTimer !== null) {
      clearTimeout(thinkingTimer)
      thinkingTimer = null
    }
  }

  function armThinkingTimeout(): void {
    clearThinkingTimeout()
    thinkingTimer = setTimeout(() => {
      thinkingTimer = null
      store.setAgentThinking(roomId, false)
      store.clearAgentStream(roomId)
      store.setAgentError(roomId, 'timeout')
    }, AGENT_THINKING_TIMEOUT_MS)
  }

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'message.created': {
        const mid = ev.message_id as string
        qc.invalidateQueries({ queryKey: ['conversation', 'messages', roomId] })
        lastSeenMessageId.value = mid
        // The persisted reply has landed — drop the transient streaming draft
        // so the bubble is replaced by the real message, never doubled.
        store.clearAgentStream(roomId)
        break
      }
      case 'message.updated':
      case 'message.deleted':
        qc.invalidateQueries({ queryKey: ['conversation', 'messages', roomId] })
        break
      case 'presence.joined':
        store.joinPresence(roomId, ev.user_id as string)
        break
      case 'presence.left':
        store.leavePresence(roomId, ev.user_id as string)
        store.removeTyping(roomId, ev.user_id as string)
        break
      case 'typing.start':
        store.addTyping(roomId, ev.user_id as string)
        break
      case 'typing.stop':
        store.removeTyping(roomId, ev.user_id as string)
        break
      case 'agent.thinking':
        store.setAgentThinking(roomId, true)
        store.clearAgentStream(roomId)
        store.setAgentError(roomId, null)
        armThinkingTimeout()
        break
      // Per-token stream from the turn engine; payload is {"text": "<delta>"}.
      case 'agent.token':
        if (typeof ev.text === 'string' && ev.text) {
          store.appendAgentToken(roomId, ev.text)
        }
        armThinkingTimeout()
        break
      case 'agent.finished':
        clearThinkingTimeout()
        store.setAgentThinking(roomId, false)
        if (typeof ev.error === 'string' && ev.error) {
          // Failed turn — no message.created will follow, so clear the draft
          // here and surface the error kind for the view to toast.
          store.clearAgentStream(roomId)
          store.setAgentError(roomId, ev.error)
        }
        break
      case 'approval.requested': {
        const approval = ev as unknown as { approval_id: string } & ApprovalWithVotes
        orchStore.upsertApproval(roomId, {
          id: approval.approval_id ?? (ev.approval_id as string),
          workflow_run_id: (ev.workflow_run_id as string) ?? '',
          mode: (ev.mode as ApprovalWithVotes['mode']) ?? 'single',
          leader_agent_id: (ev.leader_agent_id as string) ?? '',
          approver_agent_ids: (ev.approver_agent_ids as string[]) ?? [],
          timeout_seconds: (ev.timeout_seconds as number) ?? 300,
          state: 'pending',
          started_at: new Date().toISOString(),
          ended_at: null,
          votes: [],
        })
        break
      }
      case 'approval.resolved': {
        orchStore.resolveApproval(
          roomId,
          ev.approval_id as string,
          ev.state as string,
        )
        break
      }
      default:
        break
    }
  }

  const unsubscribeEvent = channel.subscribe('*', handleEvent)
  const unsubscribeStatus = channel.onStatus((isConnected) => {
    connected.value = isConnected
    if (isConnected) void replayDelta()
  })

  onMounted(() => {
    channel.connect()
  })

  onActivated(() => {
    channel.connect()
  })

  onDeactivated(() => {
    clearThinkingTimeout()
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    clearThinkingTimeout()
    channel.send({ type: 'typing.stop' })
    unsubscribeEvent()
    unsubscribeStatus()
    wsManager.close(`/chatroom/${roomId}`)
    store.resetRoom(roomId)
  })

  watch(
    () => qc.getQueryData<Message[]>(['conversation', 'messages', roomId]),
    (messages) => {
      if (messages && messages.length > 0) {
        lastSeenMessageId.value = messages[messages.length - 1]!.id
      }
    },
    { immediate: true },
  )

  return { connected, lastSeenMessageId, channel }
}

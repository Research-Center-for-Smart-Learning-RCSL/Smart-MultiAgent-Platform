// WS subscriber — the SOLE entry point for real-time updates (R24.21).
//
// Emits events into:
//   - TanStack Query (setQueryData / invalidateQueries) for message list
//   - The `useConversationStore` Pinia store for presence / ephemeral flags
//
// On reconnect (R13.20 / R24.23) we fetch `GET /messages?since=<last_id>`
// so the client recovers the delta the server did not replay.

import { useQueryClient } from '@tanstack/vue-query'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { useOrchestrationStore } from '@slices/workflow'
import type { ApprovalWithVotes } from '@slices/workflow'
import { listMessages } from '../api'
import { useConversationStore } from '../stores/conversation'
import type { Message } from '../types'

export function useChatroomSocket(roomId: string) {
  const qc = useQueryClient()
  const store = useConversationStore()
  const orchStore = useOrchestrationStore()
  const connected = ref(false)
  const lastSeenMessageId = ref<string | null>(null)

  const channel = wsManager.channel(`/chatroom/${roomId}`)

  async function replayDelta(): Promise<void> {
    if (!lastSeenMessageId.value) return
    try {
      const delta = await listMessages(roomId, { since: lastSeenMessageId.value })
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

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'message.created': {
        const mid = ev.message_id as string
        qc.invalidateQueries({ queryKey: ['conversation', 'messages', roomId] })
        lastSeenMessageId.value = mid
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
        break
      case 'agent.thinking':
        store.setAgentThinking(roomId, true)
        break
      case 'agent.finished':
        store.setAgentThinking(roomId, false)
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

  channel.subscribe('*', handleEvent)
  channel.onStatus((isConnected) => {
    connected.value = isConnected
    if (isConnected) void replayDelta()
  })

  onMounted(() => {
    channel.connect()
  })

  onBeforeUnmount(() => {
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

  return { connected, lastSeenMessageId }
}

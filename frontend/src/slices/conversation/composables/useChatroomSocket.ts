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

import { getAccessToken } from '@shared/transport'
import { useOrchestrationStore } from '@slices/workflow'
import type { ApprovalWithVotes } from '@slices/workflow'
import { listMessages } from '../api'
import { useConversationStore } from '../stores/conversation'
import type { ChatroomEvent, Message } from '../types'

const RECONNECT_DELAY_MS = 1500
const REFRESH_MARGIN_MS = 60_000 // re-auth the socket 1 min before JWT exp

export function useChatroomSocket(roomId: string) {
  const qc = useQueryClient()
  const store = useConversationStore()
  const orchStore = useOrchestrationStore()
  const connected = ref(false)
  const lastSeenMessageId = ref<string | null>(null)
  let socket: WebSocket | null = null
  let reconnectTimer: number | null = null
  let refreshTimer: number | null = null

  function wsUrl(): string {
    // Vite proxies /ws in dev; prod goes through Nginx. Relative URL
    // ensures cookies / origin checks line up.
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}/ws/chatroom/${roomId}`
  }

  function subprotocols(): string[] {
    const token = getAccessToken()
    if (!token) return []
    return [`bearer.${token}`]
  }

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

  function handleEvent(ev: ChatroomEvent): void {
    switch (ev.type) {
      case 'message.created': {
        const mid = ev.message_id as string
        // We have IDs only — refetch to hydrate content/metadata.
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

  function scheduleTokenRefresh(): void {
    if (refreshTimer !== null) clearTimeout(refreshTimer)
    refreshTimer = window.setTimeout(() => {
      const token = getAccessToken()
      if (!token || socket?.readyState !== WebSocket.OPEN) return
      socket.send(JSON.stringify({ type: 'refresh', access_token: token }))
      scheduleTokenRefresh()
    }, Math.max(30_000, REFRESH_MARGIN_MS))
  }

  function connect(): void {
    const protos = subprotocols()
    if (protos.length === 0) return // not authenticated yet
    try {
      socket = new WebSocket(wsUrl(), protos)
    } catch {
      scheduleReconnect()
      return
    }
    socket.onopen = () => {
      connected.value = true
      scheduleTokenRefresh()
      void replayDelta()
    }
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data as string) as ChatroomEvent
        handleEvent(payload)
      } catch {
        /* swallow malformed frames */
      }
    }
    socket.onclose = () => {
      connected.value = false
      if (refreshTimer !== null) {
        clearTimeout(refreshTimer)
        refreshTimer = null
      }
      scheduleReconnect()
    }
    socket.onerror = () => {
      // Let onclose drive reconnection — error fires just before close.
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer !== null) return
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      connect()
    }, RECONNECT_DELAY_MS)
  }

  onMounted(() => {
    connect()
  })

  onBeforeUnmount(() => {
    if (reconnectTimer !== null) clearTimeout(reconnectTimer)
    if (refreshTimer !== null) clearTimeout(refreshTimer)
    if (socket && socket.readyState <= WebSocket.OPEN) {
      socket.close(1000, 'unmount')
    }
    store.resetRoom(roomId)
  })

  // If the list query finished loading, capture the newest id so we have a
  // meaningful anchor for reconnect-delta replay.
  watch(
    () => qc.getQueryData<Message[]>(['conversation', 'messages', roomId]),
    (messages) => {
      if (messages && messages.length > 0) {
        lastSeenMessageId.value = messages[messages.length - 1].id
      }
    },
    { immediate: true },
  )

  return { connected, lastSeenMessageId }
}

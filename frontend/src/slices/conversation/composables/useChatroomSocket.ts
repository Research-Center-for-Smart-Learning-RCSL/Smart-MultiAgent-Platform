// WS subscriber — the SOLE entry point for real-time updates (R24.21).
//
// Emits events into:
//   - TanStack Query (setQueryData / invalidateQueries) for message list
//   - The `useConversationStore` Pinia store for presence / ephemeral flags
//
// On reconnect (R13.20 / R24.23) we fetch `GET /messages?since=<last_id>`
// so the client recovers the delta the server did not replay.

import { useQueryClient } from '@tanstack/vue-query'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { getChatroomPresence, getMessage, listMessages } from '../api'
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
  // Pill state (07-conversation / §7.1): 'connecting' before the first open,
  // 'live' while open, 'reconnecting' after a drop, and 'degraded' once the
  // socket has failed to reconnect enough times that we fall back to REST
  // polling. The transport exposes a boolean (open/closed) plus an advisory
  // degraded flag; the richer pill state is derived from both here.
  const baseState = ref<'connecting' | 'live' | 'reconnecting'>('connecting')
  const degraded = ref(false)
  const connectionState = computed<'connecting' | 'live' | 'reconnecting' | 'degraded'>(
    () => (degraded.value && baseState.value !== 'live' ? 'degraded' : baseState.value),
  )
  let everConnected = false
  const lastSeenMessageId = ref<string | null>(null)

  const channel = wsManager.channel(`/chatroom/${roomId}`)

  // Polling fallback: while the socket is degraded, pull the message delta over
  // REST every 10s so the room keeps updating even though pushes are dark (§7.1).
  const POLL_INTERVAL_MS = 10_000
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function startPolling(): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => void replayDelta(), POLL_INTERVAL_MS)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // Monotonic generation guard: each reconnect bumps this counter, so if the
  // socket flaps and two replays overlap, a slower earlier fetch cannot
  // resolve last and re-apply an older delta over fresher data (R24.23).
  let replayGeneration = 0

  async function replayDelta(): Promise<void> {
    if (!lastSeenMessageId.value) {
      // No cursor yet (empty or never-synced room): there is no delta to fetch,
      // so refetch the latest page instead. Without this a degraded-mode poll
      // would silently no-op for a room that never managed an initial sync.
      await qc.invalidateQueries({ queryKey: ['conversation', 'messages', roomId] })
      return
    }
    const generation = ++replayGeneration
    try {
      const delta = await listMessages(roomId, { since: lastSeenMessageId.value })
      if (generation !== replayGeneration) return
      for (const m of delta) applyMessageCreated(m)
    } catch {
      // BUG-8: the cursor message may have been hard-deleted → 422.
      // Fall back to a full query invalidation so TanStack refetches the
      // latest page instead of silently losing messages.
      if (generation === replayGeneration) {
        qc.invalidateQueries({ queryKey: ['conversation', 'messages', roomId] })
      }
    }
  }

  async function resyncPresence(): Promise<void> {
    try {
      const ids = await getChatroomPresence(roomId)
      store.setPresence(roomId, ids)
      store.clearTyping(roomId)
    } catch {
      // best-effort; deltas keep flowing
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
    if (m.sender_type === 'agent' && m.sender_id) {
      store.clearAgentStream(roomId, m.sender_id)
      store.clearAgentError(roomId, m.sender_id)
    }
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
      // Watchdog fires — clear ALL thinking agents in this room (we don't
      // know which specific agent is stuck when the backend is silent).
      store.clearAllAgentThinking(roomId)
      store.clearAgentStream(roomId)
      store.setAgentError(roomId, 'timeout')
    }, AGENT_THINKING_TIMEOUT_MS)
  }

  function handleEvent(ev: ChannelEvent): void {
    const agentId = (ev.agent_id as string) || (ev.sender_id as string) || ''

    switch (ev.type) {
      case 'message.created': {
        // FIX-04: delta append instead of blind invalidation so the additive
        // merge cache is never replaced with a smaller window.
        void replayDelta()
        // clearAgentError stays eager (synchronous, from the event's own
        // payload) — only clearAgentStream defers to applyMessageCreated
        // (post-append) to avoid the streamed-draft flicker.
        if (ev.sender_type === 'agent' && agentId) {
          store.clearAgentError(roomId, agentId)
        }
        break
      }
      case 'message.updated': {
        const updatedId = ev.message_id as string
        if (updatedId) {
          getMessage(updatedId)
            .then((fresh) => {
              qc.setQueryData<Message[]>(
                ['conversation', 'messages', roomId],
                (prev) => prev?.map((m) => (m.id === fresh.id ? fresh : m)),
              )
            })
            .catch(() => {})
        }
        break
      }
      case 'message.deleted': {
        const deletedId = ev.message_id as string
        if (deletedId) {
          qc.setQueryData<Message[]>(
            ['conversation', 'messages', roomId],
            (prev) => prev?.filter((m) => m.id !== deletedId),
          )
        }
        break
      }
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
        if (agentId) {
          store.setAgentThinking(roomId, agentId, true)
          store.clearAgentStream(roomId, agentId)
          // A fresh turn supersedes the prior failure — clear the badge.
          store.clearAgentError(roomId, agentId)
        }
        store.setAgentError(roomId, null)
        armThinkingTimeout()
        break
      // Per-token stream from the turn engine; payload is {"text", "agent_id"}.
      case 'agent.token':
        if (typeof ev.text === 'string' && ev.text && agentId) {
          store.appendAgentToken(roomId, agentId, ev.text)
        }
        armThinkingTimeout()
        break
      case 'agent.finished':
        clearThinkingTimeout()
        if (agentId) {
          store.setAgentThinking(roomId, agentId, false)
          // Always clear — on success the persisted message has already
          // arrived via message.created so the clear is harmless; on
          // error/empty_reply this is the only cleanup site. Clearing
          // unconditionally is safer than relying on message.created
          // delivery which can be lost during reconnect races (R7).
          store.clearAgentStream(roomId, agentId)
        }
        if (typeof ev.error === 'string' && ev.error) {
          store.setAgentError(roomId, ev.error)
          // Pin the failure to this agent so its sidebar badge stays lit until
          // it next acts (the room-level error above is consumed by the toast).
          if (agentId) {
            store.setAgentErrorKind(roomId, agentId, ev.error)
          }
        }
        // Re-arm the watchdog if other agents are still active so a
        // second agent's stuck turn still gets timed out (R1).
        if (store.isAnyAgentThinking(roomId)) {
          armThinkingTimeout()
        }
        break
      case 'approval.requested': {
        const approval = ev as unknown as { approval_id: string } & ApprovalWithVotes
        orchStore.upsertApproval(roomId, {
          id: approval.approval_id ?? (ev.approval_id as string),
          workflow_run_id: ev.workflow_run_id as string,
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
    baseState.value = isConnected
      ? 'live'
      : everConnected
        ? 'reconnecting'
        : 'connecting'
    if (isConnected) {
      everConnected = true
      stopPolling()
      store.clearAllAgentThinking(roomId)
      store.clearAgentStream(roomId)
      clearThinkingTimeout()
      void replayDelta()
      void resyncPresence()
    }
  })

  const unsubscribeDegraded = channel.onDegraded((isDegraded) => {
    degraded.value = isDegraded
    if (isDegraded) startPolling()
    else stopPolling()
  })

  onMounted(() => {
    channel.connect()
  })

  onActivated(() => {
    channel.connect()
  })

  onDeactivated(() => {
    clearThinkingTimeout()
    stopPolling()
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    clearThinkingTimeout()
    stopPolling()
    channel.send({ type: 'typing.stop' })
    unsubscribeEvent()
    unsubscribeStatus()
    unsubscribeDegraded()
    unsubCache()
    wsManager.close(`/chatroom/${roomId}`)
    store.resetRoom(roomId)
  })

  // FIX-04: seed the cursor from the query cache via a QueryCache subscription
  // (the old `watch` over `qc.getQueryData` had no reactive dependency and
  // fired exactly once while the initial fetch was in flight).
  const messagesKey = ['conversation', 'messages', roomId]
  const unsubCache = qc.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated') return
    const k = event.query.queryKey
    if (k[0] !== messagesKey[0] || k[1] !== messagesKey[1] || k[2] !== messagesKey[2]) return
    const data = event.query.state.data as Message[] | undefined
    if (!data || data.length === 0) return
    let newest = data[0]!
    for (const m of data) {
      if (m.created_at > newest.created_at) newest = m
    }
    if (
      lastSeenMessageId.value === null ||
      newest.created_at > (data.find((m) => m.id === lastSeenMessageId.value)?.created_at ?? '')
    ) {
      lastSeenMessageId.value = newest.id
    }
  })

  return { connected, connectionState, lastSeenMessageId, channel }
}

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
import { ApiError } from '@shared/api-client'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import { getActiveActivation, useActivitiesStore } from '@slices/activities'
import { getApproval } from '@slices/workflow'
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
  const activitiesStore = useActivitiesStore()
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
  let activationGeneration = 0
  let disposed = false

  // B2: a message.deleted frame can beat an in-flight create-delta HTTP
  // response back to the client (the delta was fetched from the server
  // before the delete committed). Tombstone the id so the delta's stale
  // snapshot of it doesn't get re-added to the cache once it resolves.
  // Self-evicting so this never grows unbounded across a long-lived room.
  const DELETE_TOMBSTONE_TTL_MS = 30_000
  const deletedTombstones = new Set<string>()

  function tombstoneDeletedMessage(messageId: string): void {
    deletedTombstones.add(messageId)
    setTimeout(() => deletedTombstones.delete(messageId), DELETE_TOMBSTONE_TTL_MS)
  }

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

  // Approval cards are inserted from WS alone (approval.requested), so a dropped
  // approval.resolved — or a gate whose creation rolled back before its row was
  // durable (F-18) — would pin a `pending` card until reload. Reconcile against
  // the server on (re)connect and on a slow timer; the store owns the
  // grace/decision logic, here we just supply the fetch and map a 404 to null.
  const APPROVAL_RECONCILE_INTERVAL_MS = 30_000
  let approvalReconcileTimer: ReturnType<typeof setInterval> | null = null

  async function fetchApprovalOrNull(approvalId: string): Promise<ApprovalWithVotes | null> {
    try {
      return await getApproval(approvalId)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  }

  function reconcileApprovals(): void {
    void orchStore.reconcilePending(roomId, fetchApprovalOrNull)
  }

  function startApprovalReconcile(): void {
    if (approvalReconcileTimer !== null) return
    approvalReconcileTimer = setInterval(reconcileApprovals, APPROVAL_RECONCILE_INTERVAL_MS)
  }

  function stopApprovalReconcile(): void {
    if (approvalReconcileTimer !== null) {
      clearInterval(approvalReconcileTimer)
      approvalReconcileTimer = null
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

  async function resyncActivation(): Promise<void> {
    if (disposed) return
    const generation = ++activationGeneration
    const version = activitiesStore.getActivationVersion(roomId)
    try {
      const activation = await getActiveActivation(roomId)
      if (
        disposed ||
        generation !== activationGeneration ||
        version !== activitiesStore.getActivationVersion(roomId)
      ) return
      if (activation) activitiesStore.setActivation(roomId, activation)
      else activitiesStore.clearActivation(roomId)
    } catch {
      // Best-effort: a subsequent reconnect or activation event will restore state.
    }
  }

  function applyMessageCreated(m: Message): void {
    if (!deletedTombstones.has(m.id)) {
      const key = ['conversation', 'messages', roomId]
      qc.setQueryData<Message[]>(key, (prev) => {
        if (!prev) return [m]
        if (prev.some((x) => x.id === m.id)) return prev
        return [...prev, m]
      })
    }
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
          tombstoneDeletedMessage(deletedId)
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
      // Activity submissions (R30.17). Payload is ids-only + status; the store
      // keys by submission id so a later activity.validated transitions the
      // same entry (pending -> validated/error) with no list refetch (AC-4).
      case 'activity.created': {
        const submissionId = ev.submission_id as string
        if (submissionId) {
          activitiesStore.applyCreated(roomId, {
            submissionId,
            activityTypeId: (ev.activity_type_id as string) ?? null,
            status: ev.validation_status as string,
          })
        }
        break
      }
      case 'activity.validated': {
        const submissionId = ev.submission_id as string
        if (submissionId) {
          activitiesStore.applyValidated(roomId, {
            submissionId,
            status: ev.validation_status as string,
          })
        }
        break
      }
      case 'activity.activation.started': {
        activationGeneration += 1
        const activationId = ev.activation_id as string
        const activityTypeId = ev.activity_type_id as string
        if (activationId && activityTypeId) {
          activitiesStore.setActivation(roomId, {
            id: activationId,
            activityTypeId,
            startedByUserId: (ev.started_by as string) ?? null,
          })
        }
        break
      }
      case 'activity.activation.ended': {
        activationGeneration += 1
        activitiesStore.clearActivation(roomId, ev.activation_id as string)
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
      void resyncActivation()
      // Recover any approval.resolved lost while the socket was down.
      reconcileApprovals()
    }
  })

  const unsubscribeDegraded = channel.onDegraded((isDegraded) => {
    degraded.value = isDegraded
    if (isDegraded) startPolling()
    else stopPolling()
  })

  onMounted(() => {
    channel.connect()
    startApprovalReconcile()
  })

  onActivated(() => {
    channel.connect()
    startApprovalReconcile()
  })

  onDeactivated(() => {
    clearThinkingTimeout()
    stopPolling()
    stopApprovalReconcile()
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    disposed = true
    activationGeneration += 1
    clearThinkingTimeout()
    stopPolling()
    stopApprovalReconcile()
    channel.send({ type: 'typing.stop' })
    unsubscribeEvent()
    unsubscribeStatus()
    unsubscribeDegraded()
    unsubCache()
    wsManager.close(`/chatroom/${roomId}`)
    store.resetRoom(roomId)
    activitiesStore.resetRoom(roomId)
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

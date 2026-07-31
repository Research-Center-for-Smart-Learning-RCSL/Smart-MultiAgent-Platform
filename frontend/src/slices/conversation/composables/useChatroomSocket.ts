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
import type { ActivityTypePublic } from '@slices/activities'
import { getApproval } from '@slices/workflow'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { getChatroomPresence, getMessage, listChatroomApprovals, listMessages } from '../api'
import { useConversationStore } from '../stores/conversation'
import { mergeMessages } from '../utils/mergeMessages'
import { PAGE_SIZE } from './useChatroomMessages'
import type { Message } from '../types'

// Client-side watchdog for a wedged turn: if the worker crashes mid-turn no
// `agent.finished` ever arrives, so without this the thinking spinner sticks
// forever. Re-armed by every frame that proves the turn is alive — `agent.token`
// plus `agent.progress`/`agent.warning`, which are the only ones the engine
// sends during the pre-stream assembly window — and cleared on `agent.finished`.
// It must never be the sole reading of a silence the protocol can produce
// legitimately, or a healthy turn gets reported as `timeout` (F-15).
export const AGENT_THINKING_TIMEOUT_MS = 120_000

export function useChatroomSocket(roomId: string) {
  const qc = useQueryClient()
  const store = useConversationStore()
  const orchStore = useOrchestrationStore()
  const activitiesStore = useActivitiesStore()
  const connected = ref(false)
  // Pill state (07-conversation / §7.1): 'connecting' before the first open,
  // 'live' while open, 'reconnecting' after a drop, 'degraded' once the socket
  // has failed to reconnect enough times that we fall back to REST polling, and
  // 'limited' when the server is refusing this user's excess connections
  // (R19.03). The transport exposes a boolean (open/closed) plus two advisory
  // flags; the richer pill state is derived from all three here.
  const baseState = ref<'connecting' | 'live' | 'reconnecting'>('connecting')
  const degraded = ref(false)
  const capReached = ref(false)
  const connectionState = computed<
    'connecting' | 'live' | 'reconnecting' | 'degraded' | 'limited'
  >(() => {
    if (baseState.value === 'live') return 'live'
    // 'limited' outranks 'degraded': both mean pushes are dark, but only this
    // one names something the user can act on.
    if (capReached.value) return 'limited'
    return degraded.value ? 'degraded' : baseState.value
  })
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
  // F-19: per-message generation guard for message.updated refetches (see
  // the handler below) — keyed by message id, not a single shared counter,
  // since two different messages can legitimately be mid-refetch at once.
  const messageUpdateGeneration = new Map<string, number>()
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

  // F-13: `reconcileApprovals` above can only revisit an approval id the
  // client already holds a card for — it cannot discover a gate whose
  // `approval.requested` frame was itself missed while disconnected. Fetch
  // the room's approval list on connect and seed any gate not already in
  // `liveApprovals[roomId]`; `reconcilePending` then keeps its state fresh.
  // Generation-guarded like every other connect-time async path in this
  // file — a flapping socket can overlap two discovery fetches.
  let approvalDiscoveryGeneration = 0

  async function discoverApprovals(): Promise<void> {
    const generation = ++approvalDiscoveryGeneration
    try {
      const found = await listChatroomApprovals(roomId)
      if (generation !== approvalDiscoveryGeneration) return
      for (const a of found) {
        if (!orchStore.liveApprovals[roomId]?.[a.id]) {
          orchStore.upsertApproval(roomId, a)
        }
      }
    } catch {
      // Best-effort: the 30s reconcile interval and the next connect both
      // get another chance.
    }
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

  // Shared by applyMessageCreated (live/delta path) and reconcileMessages
  // (connect-time page fetch): an agent row arriving by either path clears
  // its stream draft and any error badge the same way.
  function clearAgentSideEffects(m: Message): void {
    if (m.sender_type === 'agent' && m.sender_id) {
      store.clearAgentStream(roomId, m.sender_id)
      store.clearAgentError(roomId, m.sender_id)
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
    clearAgentSideEffects(m)
  }

  // F-11: the connect burst used to call replayDelta(), whose `since` window
  // is append-only and cannot express a deletion or an edit of an older row.
  // Fetch the current page instead and merge it through the same
  // `mergeMessages` semantics the initial query uses (FIX-04's additive
  // merge, not a raw replacement) so a message deleted while disconnected is
  // actually removed, not just left unreconciled. Shares `replayGeneration`
  // with replayDelta — both write the same cache key, and a flapping socket
  // can overlap a connect fetch with a message.created delta.
  async function reconcileMessages(): Promise<void> {
    const generation = ++replayGeneration
    try {
      // No `since`/`before`, so the backend orders this page newest-first —
      // the opposite of the since-delta's ascending order applyMessageCreated
      // assumes. Don't touch `lastSeenMessageId` here: the QueryCache
      // subscription below already recomputes it correctly from the merged
      // cache contents (order-independent), on every write to this key.
      const page = await listMessages(roomId, { limit: PAGE_SIZE })
      if (generation !== replayGeneration) return
      const key = ['conversation', 'messages', roomId]
      qc.setQueryData<Message[]>(key, (prev) => mergeMessages(prev ?? [], page))
      for (const m of page) clearAgentSideEffects(m)
    } catch {
      // Best-effort, matching resyncPresence/resyncActivation: a subsequent
      // connect or live event will reconcile.
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
      // Watchdog fires. Every thinking agent is suspect — a silent backend
      // does not say which one is stuck — but the drafts are dropped per
      // agent, read from the set before it is cleared, so a wedged agent
      // cannot take a neighbour's draft with it (F-15's aggravating factor).
      const running = [...(store.agentThinking[roomId] ?? [])]
      store.clearAllAgentThinking(roomId)
      if (running.length > 0) {
        for (const id of running) store.clearAgentStream(roomId, id)
      } else {
        // No per-agent set to scope to, but the turn has still been declared
        // dead — a draft left here has no later event to clear it.
        store.clearAgentStream(roomId)
      }
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
        // payload); the draft is left to applyMessageCreated so its clear
        // lands after the row is appended. That ordering is a flicker
        // preference, not a guarantee — `agent.finished` clears the draft
        // unconditionally (see its case), so whichever frame arrives first
        // decides, and only this path avoids the gap.
        if (ev.sender_type === 'agent' && agentId) {
          store.clearAgentError(roomId, agentId)
        }
        break
      }
      case 'message.updated': {
        const updatedId = ev.message_id as string
        if (updatedId) {
          // F-19: two message.updated frames for the same id can resolve out of
          // order; guard per id (not a single shared counter) so an unrelated
          // message's refetch cannot invalidate this one's.
          const generation = (messageUpdateGeneration.get(updatedId) ?? 0) + 1
          messageUpdateGeneration.set(updatedId, generation)
          getMessage(updatedId)
            .then((fresh) => {
              if (messageUpdateGeneration.get(updatedId) !== generation) return
              qc.setQueryData<Message[]>(
                ['conversation', 'messages', roomId],
                (prev) => prev?.map((m) => (m.id === fresh.id ? fresh : m)),
              )
            })
            .catch(() => {})
            .finally(() => {
              // Evict once settled, matching the self-evicting deletedTombstones
              // pattern above — otherwise this map grows for every distinct
              // edited message id for the life of the room.
              if (messageUpdateGeneration.get(updatedId) === generation) {
                messageUpdateGeneration.delete(updatedId)
              }
            })
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
      // Non-terminal notice from a turn that is still running (R13.19) — the
      // engine's only frame during the pre-stream assembly window. It carries
      // no UI of its own yet, but it is proof of liveness, so it re-arms rather
      // than falling to `default` and letting the watchdog count a healthy turn
      // down to a false `timeout`.
      case 'agent.warning':
        armThinkingTimeout()
        break
      // The turn's liveness beacon through the assembly window (R13.19).
      // One phase means more than "alive": at a tool-round boundary the text
      // streamed during that round has been superseded — only the final round
      // is persisted — so the draft resets to show the current round alone.
      // Without it the rounds accumulate and are then replaced wholesale at
      // `agent.finished`, which is the flash 07-conversation.md forbids (F-40).
      case 'agent.progress':
        if (ev.phase === 'tool_round' && agentId) {
          store.clearAgentStream(roomId, agentId)
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
            activityType: (ev.activity_type as ActivityTypePublic | undefined) ?? null,
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
      void reconcileMessages()
      void resyncPresence()
      void resyncActivation()
      // Discover a gate raised entirely while disconnected (F-13), and
      // recover any approval.resolved lost while the socket was down.
      void discoverApprovals()
      reconcileApprovals()
    }
  })

  const unsubscribeDegraded = channel.onDegraded((isDegraded) => {
    degraded.value = isDegraded
    if (isDegraded) startPolling()
    else stopPolling()
  })

  // Only feeds the pill — the polling fallback stays keyed off `degraded`,
  // which a capped channel also reaches on its third refused attempt.
  const unsubscribeCapReached = channel.onCapReached((isCapReached) => {
    capReached.value = isCapReached
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
    unsubscribeCapReached()
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

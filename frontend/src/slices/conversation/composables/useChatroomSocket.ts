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
// `@shared/errors`, NOT the generated client's same-named class: the axios
// response interceptor converts every problem+json body into a `@shared/errors`
// subclass and rejects with it, and the generated client rethrows that untouched
// (its own `catchErrorCodes` never runs, because the rejected value carries no
// `.response`). An `instanceof` against the generated `ApiError` is therefore
// always false — both classes expose `.status`, which is what hid it.
import { ApiError } from '@shared/errors'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import { getActiveActivation, useActivitiesStore } from '@slices/activities'
import type { ActivityTypePublic } from '@slices/activities'
import { getApproval } from '@slices/workflow'
import type { ApprovalWithVotes } from '@shared/types/workflow'
import { getChatroomPresence, getMessage, listChatroomApprovals, listMessages } from '../api'
import { convKeys } from '../queries'
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

// Placeholder date for an `approval.requested` frame from a backend old enough
// to omit `started_at`. Deliberately unparseable: `Date.parse` returns NaN, and
// the feed's merge maps that to the tail rather than to a position it cannot
// justify. Any parseable stand-in — the client's own clock above all — buys
// plausibility at the cost of putting a gate somewhere it does not belong.
export const UNKNOWN_STARTED_AT = 'unknown'

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

  // F-15: `agent.token` arrives once per token, and the store reassigns
  // `agentStreams` immutably on every write — so each token cost a render, a
  // `renderMarkdown` (markdown-it plus DOMPurify) and a full `v-html` subtree
  // replacement. Buffering per agent and writing at most once per window
  // collapses that at its source, which is why this lives here and not in
  // `useAgentStreams`: every consumer of `agentStreams` benefits, not just the
  // bubble. 120ms is the interval 12-shared-patterns.md:474 specifies.
  const TOKEN_FLUSH_MS = 120
  const tokenBuffer = new Map<string, string>()
  let tokenFlushTimer: ReturnType<typeof setTimeout> | null = null

  function flushAgentTokens(): void {
    if (tokenFlushTimer !== null) {
      clearTimeout(tokenFlushTimer)
      tokenFlushTimer = null
    }
    if (tokenBuffer.size === 0) return
    const pending = [...tokenBuffer]
    tokenBuffer.clear()
    for (const [agentId, text] of pending) store.appendAgentToken(roomId, agentId, text)
  }

  function bufferAgentToken(agentId: string, text: string): void {
    tokenBuffer.set(agentId, (tokenBuffer.get(agentId) ?? '') + text)
    if (tokenFlushTimer === null) {
      tokenFlushTimer = setTimeout(flushAgentTokens, TOKEN_FLUSH_MS)
    }
  }

  /** Reset a stream draft, draining the buffer first.
   *
   *  Every reset must go through here. A token buffered before a reset would
   *  otherwise be written after it and resurrect the draft the reset exists to
   *  remove — a ghost bubble, which is worse than the churn being fixed. There
   *  are six reset sites and routing them through one function is what stops a
   *  seventh from missing the rule. */
  function resetAgentStream(agentId?: string): void {
    flushAgentTokens()
    store.clearAgentStream(roomId, agentId)
  }

  // Shared by applyMessageCreated (live/delta path) and reconcileMessages
  // (connect-time page fetch): an agent row arriving by either path clears
  // its stream draft and any error badge the same way.
  function clearAgentSideEffects(m: Message): void {
    if (m.sender_type === 'agent' && m.sender_id) {
      resetAgentStream(m.sender_id)
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
        for (const id of running) resetAgentStream(id)
      } else {
        // No per-agent set to scope to, but the turn has still been declared
        // dead — a draft left here has no later event to clear it.
        resetAgentStream()
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
      // F-1 (R28.09). The room DTO carries `observers_present`, and nothing
      // could invalidate it: the singular `convKeys.chatroom` is not
      // prefix-matched by the plural `['conversation','chatrooms']` every other
      // invalidation names, and no writer published anything to begin with. So
      // a participant learned they were being observed only on reload.
      //
      // The frame is ids-only by construction — the room channel fans out to
      // every subscriber including guests — so the answer comes from re-reading
      // `GET /chatrooms/{id}`, whose `_to_out` re-applies the guest
      // neutralisation per viewer. `chatroom-agents` rides along because a role
      // change moves the roster too (F-10c), and it has no other invalidator.
      case 'chatroom.updated': {
        if (ev.chatroom_id !== roomId) break
        void qc.invalidateQueries({ queryKey: convKeys.chatroom(roomId) })
        void qc.invalidateQueries({ queryKey: convKeys.chatroomAgents(roomId) })
        // F-1. The roster refetch above brings a newly bound agent into the rail;
        // its *name* comes from a project-scoped query that nothing invalidated,
        // so the agent arrived as an 8-char id — and because mention resolution
        // matches on `agent.name`, `@RealName` resolved to nothing and the agent
        // was silently never woken. A prefix, because this handler has the room
        // id and not the project id, and because the call site's computed key
        // transiently holds `[..., undefined]` before the workspace read lands.
        void qc.invalidateQueries({ queryKey: convKeys.projectAgentsAll() })
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
          resetAgentStream(agentId)
          // A fresh turn supersedes the prior failure — clear the badge.
          store.clearAgentError(roomId, agentId)
        }
        store.setAgentError(roomId, null)
        armThinkingTimeout()
        break
      // Per-token stream from the turn engine; payload is {"text", "agent_id"}.
      case 'agent.token':
        if (typeof ev.text === 'string' && ev.text && agentId) {
          bufferAgentToken(agentId, ev.text)
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
          resetAgentStream(agentId)
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
          resetAgentStream(agentId)
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
        // The feed interleaves this card with server-dated messages, so its
        // date has to come from the same clock. There is deliberately no
        // client-clock fallback: on a machine running behind the server, an
        // invented date sorts a gate the user must vote on above the last N
        // messages -- off-screen, and without moving the tail, so the unseen
        // pill does not fire either. UNKNOWN_STARTED_AT is unparseable, which
        // `feedItems` maps to the tail, and `reconcilePending` replaces from
        // the authoritative row.
        const startedAt = ev.started_at
        const dated = typeof startedAt === 'string' && !Number.isNaN(Date.parse(startedAt))
        orchStore.upsertApproval(roomId, {
          id: approval.approval_id ?? (ev.approval_id as string),
          workflow_run_id: ev.workflow_run_id as string,
          mode: (ev.mode as ApprovalWithVotes['mode']) ?? 'single',
          leader_agent_id: (ev.leader_agent_id as string) ?? '',
          approver_agent_ids: (ev.approver_agent_ids as string[]) ?? [],
          timeout_seconds: (ev.timeout_seconds as number) ?? 300,
          state: 'pending',
          started_at: dated ? startedAt : UNKNOWN_STARTED_AT,
          ended_at: null,
          votes: [],
        })
        // Otherwise the placeholder outlives the gate: reconciliation is
        // driven from reconnect alone, so on a socket that never drops the
        // card would keep an unknown date -- and an unknown deadline -- for
        // its whole life. `reconcilePending` skips a card still inside its
        // grace window, which every correctly dated card is at this moment,
        // so this pass costs one request for the placeholder and nothing for
        // the normal path.
        if (!dated) reconcileApprovals()
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
            // Present only for a delegated round ([R30.37]); the panel names the
            // agent so the class can see who paced it.
            startedByAgentId: (ev.started_by_agent_id as string | undefined) ?? null,
            startedByAgentName: (ev.started_by_agent_name as string | undefined) ?? null,
          })
        }
        break
      }
      case 'activity.activation.ended': {
        activationGeneration += 1
        activitiesStore.clearActivation(roomId, ev.activation_id as string)
        // Ending a round expires every open proposal for it ([R30.41]/AC-9), and
        // the end broadcast is the only announcement of that (dossier FU-8) — so
        // the cards go with the activation rather than waiting for a per-proposal
        // event that this path deliberately does not send.
        activitiesStore.clearProposals(roomId, ev.activation_id as string)
        break
      }
      // Group proposals ([R30.42]): ids, a status and counts — never the payload
      // and never a per-person vote. The store updates only a proposal this
      // client was already authorised to see; an unrecognised one is recorded by
      // group id for the panel to decide whether re-reading is its business.
      case 'activity.proposal.opened':
      case 'activity.proposal.voted':
      case 'activity.proposal.resolved': {
        const proposalId = ev.proposal_id as string
        if (proposalId) {
          activitiesStore.applyProposalEvent(roomId, {
            proposalId,
            memberGroupId: (ev.member_group_id as string | undefined) ?? null,
            status: ev.status as string,
            requiredApprovals: (ev.required_approvals as number | undefined) ?? null,
            approvals: (ev.approvals as number | undefined) ?? null,
            rejections: (ev.rejections as number | undefined) ?? null,
            undecided: (ev.undecided as number | undefined) ?? null,
            voterCount: (ev.voter_count as number | undefined) ?? null,
          })
        }
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
      const isReconnect = everConnected
      everConnected = true
      stopPolling()
      store.clearAllAgentThinking(roomId)
      resetAgentStream()
      clearThinkingTimeout()
      void reconcileMessages()
      void resyncPresence()
      void resyncActivation()
      // F-3. `chatroom.updated` is these two keys' only invalidator, and Redis
      // pub/sub does not replay — a frame published while this socket was down
      // is gone for good. Without this the disclosure chip stays dark for the
      // rest of a focused session, which is the F-1 symptom reached through the
      // reconnect door rather than the missing-handler door. The frame remains
      // the fast path; this is the delivery it never had.
      //
      // Gated on a *re*connect, unlike the reconciles above, because these two
      // are `invalidateQueries` rather than merges: it defaults `cancelRefetch`
      // to true, so firing on the first connect would abort and restart the very
      // fetches `ChatroomView` issued on mount whenever the handshake lands
      // while they are still in flight. On a first connect there is nothing to
      // recover — the queries have just run.
      if (isReconnect) {
        void qc.invalidateQueries({ queryKey: convKeys.chatroom(roomId) })
        void qc.invalidateQueries({ queryKey: convKeys.chatroomAgents(roomId) })
      }
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
    // The view is cached, not destroyed, so the buffered tail is still wanted;
    // flushing also cancels the timer, which must not outlive the socket.
    flushAgentTokens()
    clearThinkingTimeout()
    stopPolling()
    stopApprovalReconcile()
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    disposed = true
    activationGeneration += 1
    // Cancels the pending window as well as draining it: a timer surviving
    // unmount would write into a room whose state was just reset.
    flushAgentTokens()
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

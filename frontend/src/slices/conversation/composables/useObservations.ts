// Creator-only observation feed for a chatroom (SRS §28).
//
// Live updates ride the shared /ws/user/{id} channel. IMPORTANT: that channel
// is a WsManager singleton with no refcounting and useBanKickGuard owns its
// lifecycle — this composable is an ADDITIVE subscriber (subscribe + connect()
// only; teardown unsubscribes its own handlers and must never close()).
//
// The observations query is gated on `isCreator`: the endpoint 403s for
// everyone else (R28.03), and a speculative request would just pollute error
// handling. The client never *enforces* anything — the server already
// filtered; absence of data here means "you are not told".

import { computed, ref, watch, onScopeDispose, type Ref } from 'vue'
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import {
  deleteObservation,
  listObservations,
  releaseObservation,
  type BoundAgentRef,
  type ReleaseBody,
} from '../api'
import { convKeys } from '../queries'
import { useConversationStore } from '../stores/conversation'
// Shared with the room path's wedged-turn watchdog on purpose: two surfaces
// that declare a silent backend dead at different deadlines are two behaviours
// nobody can explain together.
import { AGENT_THINKING_TIMEOUT_MS } from './useChatroomSocket'
import type { Chatroom, Observation } from '../types'

const PAGE_SIZE = 50

export interface ObserverEntry {
  id: string
  name: string
  // 'unknown' is not a worker state: it says this viewer has no status feed at
  // all (F-6). Everything else is written by a WS handler, so for a viewer who
  // receives no events the old 'idle' fall-through was an affirmative claim
  // about a turn they cannot hear.
  status: 'analyzing' | 'error' | 'skipped' | 'idle' | 'unknown'
  errorReason?: string
  skipReason?: string
}

export interface UseObservationsOptions {
  room: Ref<Chatroom | undefined>
  // No `projectId`: it existed solely to key the member-list query F-3 deleted.
  // Leaving it would advertise a dependency this composable no longer has.
  boundAgents: Ref<BoundAgentRef[] | undefined>
  agentNames: Ref<Record<string, string>>
}

export function useObservations(chatroomId: string, opts: UseObservationsOptions) {
  const session = useSessionStore()
  const store = useConversationStore()
  const qc = useQueryClient()

  // ---- creator resolution (R28.02 mirror; server is authoritative) --------
  //
  // F-3: this reads the server's own published answer instead of re-deriving
  // one. For a NULL-creator room the backend falls back to moderator semantics,
  // where an inherited ORG_OWNER role counts with **no `project_members` row at
  // all** — so the old member-list scan locked out precisely the owner the
  // server would have admitted, and its unpaginated fetch dropped a genuine
  // owner past the default limit of 100 besides. `is_moderator` is on the DTO
  // for exactly this case, and `useProjectRole` already made the same move.
  //
  // The fallback stays confined to NULL-creator rooms: a non-creator moderator
  // of a room somebody else created must not read its observations (R28.02),
  // and widening here would paint a surface whose every request then 403s.

  const isCreator = computed<boolean>(() => {
    const me = session.me
    const room = opts.room.value
    if (!me || !room) return false
    if (me.is_admin) return true
    if (room.created_by_user_id !== null) return room.created_by_user_id === me.id
    return room.is_moderator === true
  })

  // ---- observer roster ------------------------------------------------------

  // The event-delivery predicate, deliberately not the authorization one.
  // `_emit_observation_event` addresses `user_channel(room.created_by_user_id)`
  // literally and publishes nothing when that id is None, so an admin who *is*
  // the creator keeps live status while an admin who is not — and a NULL-creator
  // room's moderator-fallback viewer — receives no observation.* frame ever.
  // Also what W-1's 30s poll keys off, so the two cannot drift.
  const receivesLiveStatus = computed<boolean>(
    () =>
      opts.room.value?.created_by_user_id != null &&
      session.me?.id === opts.room.value.created_by_user_id,
  )

  const observerAgents = computed<ObserverEntry[]>(() =>
    (opts.boundAgents.value ?? [])
      .filter((a) => a.role === 'observer')
      .map((a) => {
        const analyzing = store.observerAnalyzing[chatroomId]?.has(a.agent_id)
        const errorReason = store.observerErrors[chatroomId]?.[a.agent_id]
        const skipReason = store.observerSkips[chatroomId]?.[a.agent_id]
        return {
          id: a.agent_id,
          name: opts.agentNames.value[a.agent_id] ?? a.agent_id.slice(0, 8),
          status: analyzing
            ? 'analyzing'
            : errorReason
              ? 'error'
              : skipReason
                ? 'skipped'
                : receivesLiveStatus.value
                  ? 'idle'
                  : 'unknown',
          ...(errorReason !== undefined && { errorReason }),
          ...(skipReason !== undefined && { skipReason }),
        }
      }),
  )

  // ---- observations (newest-first keyset pages) ------------------------------

  const observationsQuery = useInfiniteQuery({
    queryKey: convKeys.observations(chatroomId),
    queryFn: ({ pageParam }) =>
      listObservations(chatroomId, {
        limit: PAGE_SIZE,
        ...(pageParam ? { before: pageParam } : {}),
      }),
    initialPageParam: '',
    getNextPageParam: (lastPage) =>
      lastPage.length === PAGE_SIZE ? lastPage[lastPage.length - 1]!.id : undefined,
    enabled: isCreator,
    retry: false,
    // W-1 (R28.13): observation.* events go only to the literal creator's
    // user channel. Admins and NULL-creator moderator-fallback viewers pass
    // the REST gate but never receive events — poll for them so the panel is
    // not silently stale. The real creator keeps the pure WS path.
    refetchInterval: () => (isCreator.value && !receivesLiveStatus.value ? 30_000 : false),
  })

  const observations = computed<Observation[]>(
    () => observationsQuery.data.value?.pages.flat() ?? [],
  )

  // The Observer tab's existence must track the data (creator has something to
  // read/release/delete), not the roster's liveness — observations outlive the
  // observer binding that produced them (docs/tasks/2026-07-22-observation-binding-cleanup).
  const hasObserverSurface = computed<boolean>(
    () => isCreator.value && (observerAgents.value.length > 0 || observations.value.length > 0),
  )

  // ---- live updates over /ws/user/{id} (R28.13) -------------------------------

  const panelOpen = ref(false)
  const unreadCount = ref(0)

  function setPanelOpen(open: boolean): void {
    panelOpen.value = open
    if (open) unreadCount.value = 0
  }

  // F-13. The badge counted only WS arrivals, so an `observation.created` lost
  // to a socket gap raised nothing even after the reconnect refetch recovered
  // the row. The high-water mark is a timestamp rather than an id position: a
  // row can leave the page (deleted, or paged off the end), and a positional
  // comparison would then read the whole page as new.
  const newestSeenAt = ref<string | null>(null)

  /** Raise the mark. Monotonic by construction, so a deleted row or a page that
   *  scrolls off cannot lower it and make known rows look new again. */
  function noteSeen(createdAt: unknown): void {
    if (typeof createdAt !== 'string') return
    if (newestSeenAt.value === null || createdAt > newestSeenAt.value) {
      newestSeenAt.value = createdAt
    }
  }

  // `immediate` because a mount can begin with rows already in hand: returning to
  // a room inside `gcTime` serves the query from cache, and a lazy watcher would
  // leave the mark null until the mount refetch wrote the cache again. A reconnect
  // landing in that window reads `baseline === null` and returns, so every
  // observation written during the outage goes unbadged — the gap F-13 exists to
  // close, reopened by the one case where the panel already had rows.
  watch(
    observations,
    (rows) => {
      for (const o of rows) noteSeen(o.created_at)
    },
    { immediate: true },
  )

  // F-8's reconnect half, mirroring the room path's onStatus reconcile.
  async function reconcileOnReconnect(): Promise<void> {
    clearAnalyzingWatchdog()
    // Every terminal frame published while the socket was down is gone —
    // Redis pub/sub does not replay — so nothing else will ever clear these.
    store.clearAllObserverAnalyzing(chatroomId)
    // The `enabled` gate does NOT cover an explicit refetch: TanStack fetches a
    // disabled query when asked directly. Every viewer subscribes to this
    // channel, so without this guard each reconnect would put a speculative
    // 403 on the wire for everyone but the creator (R28.03).
    if (!isCreator.value) return
    // Read the mark BEFORE the refetch: the watcher above runs on the cache
    // write this refetch performs, and a mark read afterwards would already
    // include the very rows being counted.
    const baseline = newestSeenAt.value
    let rows: Observation[] = []
    try {
      rows = (await observationsQuery.refetch()).data?.pages.flat() ?? []
    } catch {
      // Best-effort, matching the room path's reconcileMessages: the poll, the
      // next live event or the next reconnect reconciles instead. The panel's
      // own error banner (F-7) is what reports a persistently dead query.
      return
    }
    if (baseline === null) return
    const fresh = rows.filter(
      (o) => o.created_at !== null && o.created_at > baseline,
    ).length
    if (fresh > 0 && !panelOpen.value) unreadCount.value += fresh
  }

  // F-8's watchdog half. A worker killed between the started emit and the
  // terminal emit leaves no frame to lose and no reconnect to recover, so only
  // a deadline resolves it. One timer per room, not per agent: a silent backend
  // does not say which observer is stuck, and the room path makes the same
  // trade at the same 120s.
  let analyzingTimer: ReturnType<typeof setTimeout> | null = null

  function clearAnalyzingWatchdog(): void {
    if (analyzingTimer !== null) {
      clearTimeout(analyzingTimer)
      analyzingTimer = null
    }
  }

  function armAnalyzingWatchdog(): void {
    clearAnalyzingWatchdog()
    analyzingTimer = setTimeout(() => {
      analyzingTimer = null
      const running = [...(store.observerAnalyzing[chatroomId] ?? [])]
      store.clearAllObserverAnalyzing(chatroomId)
      for (const id of running) store.setObserverErrorKind(chatroomId, id, 'timeout')
    }, AGENT_THINKING_TIMEOUT_MS)
  }

  /** Any terminal frame proves the turn ended; only re-arm while one is live. */
  function disarmIfIdle(): void {
    if ((store.observerAnalyzing[chatroomId]?.size ?? 0) === 0) clearAnalyzingWatchdog()
  }

  const unsubs: Array<() => void> = []

  function teardown(): void {
    for (const u of unsubs.splice(0)) u()
    clearAnalyzingWatchdog()
  }

  watch(
    () => session.me?.id,
    (userId) => {
      teardown()
      if (!userId) return
      const channel = wsManager.channel(`/user/${userId}`)

      const forThisRoom = (ev: ChannelEvent): boolean => ev.chatroom_id === chatroomId

      unsubs.push(
        channel.subscribe('observation.started', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          const agentId = ev.agent_id as string
          store.setObserverAnalyzing(chatroomId, agentId, true)
          store.clearObserverError(chatroomId, agentId)
          store.clearObserverSkip(chatroomId, agentId)
          armAnalyzingWatchdog()
        }),
        channel.subscribe('observation.skipped', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          const agentId = ev.agent_id as string
          store.setObserverAnalyzing(chatroomId, agentId, false)
          store.setObserverSkipKind(chatroomId, agentId, String(ev.kind ?? 'skipped'))
          disarmIfIdle()
        }),
        channel.subscribe('observation.created', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          const agentId = ev.agent_id as string
          store.setObserverAnalyzing(chatroomId, agentId, false)
          // The output disproves any prior verdict about this turn — above all
          // a watchdog `timeout` on an analysis that was merely slow, which
          // would otherwise label the observer failed for the rest of the
          // page's life while its own result sits in the list below. §9 accepts
          // the false positive precisely because this clears it. Mirrors the
          // room path clearing agent errors on `message.created`.
          store.clearObserverError(chatroomId, agentId)
          store.clearObserverSkip(chatroomId, agentId)
          disarmIfIdle()
          // Advance the high-water mark from the frame, not from the refetch it
          // schedules: the badge rises synchronously here while the cache write
          // is async, so a reconnect landing in that gap would re-count this
          // very row against a mark that had not moved yet.
          noteSeen(ev.created_at)
          // Payload is ids-only; the body comes from REST (same discipline as
          // message.created → delta refetch).
          void qc.invalidateQueries({ queryKey: convKeys.observations(chatroomId) })
          if (!panelOpen.value) unreadCount.value += 1
        }),
        channel.subscribe('observation.failed', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          const agentId = ev.agent_id as string
          store.setObserverAnalyzing(chatroomId, agentId, false)
          store.setObserverErrorKind(chatroomId, agentId, String(ev.kind ?? 'failed'))
          disarmIfIdle()
        }),
        channel.subscribe('observation.released', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          patchReleased(String(ev.observation_id), ev.target as Observation['release_target'])
        }),
        // F-14. Another session of this creator deleted the row; without this
        // frame the panel kept rendering it — and offering Release and Delete
        // on it — until a focus refetch happened to land.
        channel.subscribe('observation.deleted', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          dropObservation(String(ev.observation_id))
        }),
        // F-1/F-10(c) for the creator's OTHER sessions. The room-channel copy of
        // this event is withheld when the write is invisible to non-creators —
        // binding an observer while disclosure is off, say — because its mere
        // arrival would tell every participant and guest that something they
        // cannot see just happened. The creator is owed the refresh regardless,
        // so it reaches them here, on the private channel the observation.*
        // events already use. Binding in tab B updates tab A.
        channel.subscribe('chatroom.updated', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          void qc.invalidateQueries({ queryKey: convKeys.chatroom(chatroomId) })
          void qc.invalidateQueries({ queryKey: convKeys.chatroomAgents(chatroomId) })
        }),
      )
      // F-8/F-13. `onStatus` does not push the current value on subscribe
      // (unlike onDegraded), so this fires on a real transition only and a
      // fresh mount is never mistaken for a reconnect.
      unsubs.push(
        channel.onStatus((isConnected: boolean) => {
          if (isConnected) void reconcileOnReconnect()
        }),
      )
      // Idempotent — ban-kick / notifications likely connected it already.
      channel.connect()
    },
    { immediate: true },
  )

  onScopeDispose(teardown)

  /** Immutable in-place patch of one cached observation — replacing objects,
   *  never mutating them (in-place mutation does not retrigger computeds). */
  function patchReleased(
    observationId: string,
    target: Observation['release_target'],
  ): void {
    qc.setQueryData<{ pages: Observation[][]; pageParams: unknown[] }>(
      convKeys.observations(chatroomId),
      (data) => {
        if (!data) return data
        return {
          ...data,
          pages: data.pages.map((page) =>
            page.map((o) =>
              o.id === observationId
                ? { ...o, released_at: o.released_at ?? new Date().toISOString(), release_target: target }
                : o,
            ),
          ),
        }
      },
    )
  }

  /** Filter one row out of the cache and re-establish an authoritative page
   *  window. W-5: the filter can shrink a full last page below PAGE_SIZE, which
   *  flips the length-based `getNextPageParam` to "no more pages" while older
   *  rows still exist server-side. */
  function dropObservation(observationId: string): void {
    qc.setQueryData<{ pages: Observation[][]; pageParams: unknown[] }>(
      convKeys.observations(chatroomId),
      (data) =>
        data
          ? { ...data, pages: data.pages.map((p) => p.filter((o) => o.id !== observationId)) }
          : data,
    )
    void qc.invalidateQueries({ queryKey: convKeys.observations(chatroomId) })
  }

  // ---- actions ----------------------------------------------------------------

  async function release(observationId: string, body: ReleaseBody): Promise<Observation> {
    const released = await releaseObservation(chatroomId, observationId, body)
    patchReleased(released.id, released.release_target)
    // F-11: the list endpoint does not filter released rows, so a poll response
    // issued before this release still carries `released_at: null` and would
    // overwrite the patch when it lands. The optimistic patch is what keeps the
    // UI instant; the invalidate is what makes the server the last writer.
    // Same discipline `remove()` has had since W-5.
    void qc.invalidateQueries({ queryKey: convKeys.observations(chatroomId) })
    return released
  }

  async function remove(observationId: string): Promise<void> {
    await deleteObservation(chatroomId, observationId)
    dropObservation(observationId)
  }

  return {
    isCreator,
    observerAgents,
    observations,
    hasObserverSurface,
    observationsLoading: computed(() => observationsQuery.isLoading.value),
    // F-7: `retry: false` plus the `?? []` collapse above made a dead query
    // indistinguishable from an empty room, and the panel then asserted the
    // positive ("Observers write here after they analyze the conversation") on
    // the strength of a request that never landed. No global QueryCache.onError
    // covers for it — the shared client installs none.
    observationsError: computed(() => observationsQuery.isError.value),
    hasMore: computed(() => observationsQuery.hasNextPage.value ?? false),
    loadingMore: computed(() => observationsQuery.isFetchingNextPage.value),
    loadEarlier: () => observationsQuery.fetchNextPage(),
    refetch: () => observationsQuery.refetch(),
    unreadCount,
    setPanelOpen,
    release,
    remove,
  }
}

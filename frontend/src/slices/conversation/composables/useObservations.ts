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
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { useSessionStore } from '@shared/stores/session'
import { tenancyKeys, projectsApi } from '@slices/tenancy'
import {
  deleteObservation,
  listObservations,
  releaseObservation,
  type BoundAgentRef,
  type ReleaseBody,
} from '../api'
import { convKeys } from '../queries'
import { useConversationStore } from '../stores/conversation'
import type { Chatroom, Observation } from '../types'

const PAGE_SIZE = 50

export interface ObserverEntry {
  id: string
  name: string
  status: 'analyzing' | 'error' | 'skipped' | 'idle'
  errorReason?: string
  skipReason?: string
}

export interface UseObservationsOptions {
  room: Ref<Chatroom | undefined>
  projectId: Ref<string | undefined>
  boundAgents: Ref<BoundAgentRef[] | undefined>
  agentNames: Ref<Record<string, string>>
}

export function useObservations(chatroomId: string, opts: UseObservationsOptions) {
  const session = useSessionStore()
  const store = useConversationStore()
  const qc = useQueryClient()

  // ---- creator resolution (R28.02 mirror; server is authoritative) --------

  const membersQuery = useQuery({
    queryKey: computed(() => tenancyKeys.projectMembers(opts.projectId.value ?? '')),
    queryFn: () => projectsApi.listMembers(opts.projectId.value!),
    // Only needed for the legacy NULL-creator fallback — skip otherwise.
    enabled: computed(
      () => !!opts.projectId.value && !!opts.room.value && opts.room.value.created_by_user_id === null,
    ),
    retry: false,
  })

  const isCreator = computed<boolean>(() => {
    const me = session.me
    const room = opts.room.value
    if (!me || !room) return false
    if (me.is_admin) return true
    if (room.created_by_user_id !== null) return room.created_by_user_id === me.id
    const membership = membersQuery.data.value?.find((m) => m.user_id === me.id)
    return membership?.role === 'owner'
  })

  // ---- observer roster ------------------------------------------------------

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
          status: analyzing ? 'analyzing' : errorReason ? 'error' : skipReason ? 'skipped' : 'idle',
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
    refetchInterval: () =>
      isCreator.value && session.me?.id !== opts.room.value?.created_by_user_id ? 30_000 : false,
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

  const unsubs: Array<() => void> = []

  function teardown(): void {
    for (const u of unsubs.splice(0)) u()
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
        }),
        channel.subscribe('observation.skipped', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          const agentId = ev.agent_id as string
          store.setObserverAnalyzing(chatroomId, agentId, false)
          store.setObserverSkipKind(chatroomId, agentId, String(ev.kind ?? 'skipped'))
        }),
        channel.subscribe('observation.created', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          store.setObserverAnalyzing(chatroomId, ev.agent_id as string, false)
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
        }),
        channel.subscribe('observation.released', (ev: ChannelEvent) => {
          if (!forThisRoom(ev)) return
          patchReleased(String(ev.observation_id), ev.target as Observation['release_target'])
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

  // ---- actions ----------------------------------------------------------------

  async function release(observationId: string, body: ReleaseBody): Promise<Observation> {
    const released = await releaseObservation(chatroomId, observationId, body)
    patchReleased(released.id, released.release_target)
    return released
  }

  async function remove(observationId: string): Promise<void> {
    await deleteObservation(chatroomId, observationId)
    qc.setQueryData<{ pages: Observation[][]; pageParams: unknown[] }>(
      convKeys.observations(chatroomId),
      (data) =>
        data
          ? { ...data, pages: data.pages.map((p) => p.filter((o) => o.id !== observationId)) }
          : data,
    )
    // W-5: the optimistic filter can shrink a full last page below PAGE_SIZE,
    // which flips the length-based getNextPageParam to "no more pages" while
    // older rows still exist server-side. Refetch to restore an authoritative
    // hasNextPage (same discipline as the observation.created handler).
    void qc.invalidateQueries({ queryKey: convKeys.observations(chatroomId) })
  }

  return {
    isCreator,
    observerAgents,
    observations,
    hasObserverSurface,
    observationsLoading: computed(() => observationsQuery.isLoading.value),
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

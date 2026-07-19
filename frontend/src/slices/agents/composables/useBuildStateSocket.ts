// Shared real-time build-progress engine behind useGraphragSocket and
// useKnowmapSocket — both subscribe to a per-config WS channel carrying a
// single `build.state` event (not useRagConfigSocket's multi-stage
// ingestion protocol), with a 15s backstop poll for a dropped terminal
// event and a config-scoped teardown. The only real differences between
// the two callers are the WS path prefix, how to re-fetch a config's
// status for the backstop, and which query keys a terminal state
// invalidates — everything else (the poll lifecycle, the watch/unwatch
// bookkeeping, the seed-state race guard) is identical and lives here once.

import { useQueryClient, type QueryKey } from '@tanstack/vue-query'
import { onBeforeUnmount, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { GRAPHRAG_BUILD_STATES, GRAPHRAG_IN_PROGRESS, type GraphragBuildState } from '../api'

const POLL_FALLBACK_MS = 15000

// Derived from the state list itself, so a state added to the union is accepted
// here automatically. The previous hand-listed whitelist would have dropped
// `build.state` events carrying any new state, leaving the UI on a stale badge
// until the next REST refetch.
function isBuildState(s: string): s is GraphragBuildState {
  return (GRAPHRAG_BUILD_STATES as readonly string[]).includes(s)
}

export interface BuildStateSocketOptions {
  /** WS channel path prefix, e.g. '/graphrag' or '/knowmap'; joined with `/${configId}`. */
  pathPrefix: string
  /** Backstop re-fetch of a config's current build state (no dedicated /status route for every caller). */
  fetchStatus: (configId: string) => Promise<GraphragBuildState>
  /** Query keys to invalidate once a config's build reaches a terminal state. */
  invalidateKeysOnTerminal: (configId: string) => QueryKey[]
}

export function useBuildStateSocket(options: BuildStateSocketOptions) {
  const qc = useQueryClient()
  // configId -> latest live build state.
  const liveState = ref<Record<string, GraphragBuildState>>({})
  // configId -> monotonic counter bumped on every applied state (a WS
  // build.state frame or a backstop resync). A resync captures it before
  // awaiting fetchStatus and drops its result if a newer state landed meanwhile,
  // so a slow REST resync can never overwrite a fresher WS frame — the B1 race
  // where a stale `running` resync, resolving after a terminal frame closed the
  // channel, stranded a row as in-progress with no live channel.
  const applySeq = new Map<string, number>()
  // configId -> teardown for its channel subscription.
  const watched = new Map<string, () => void>()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function isInProgress(configId: string): boolean {
    // Only poll configs with a known in-progress state. Unknown (undefined)
    // must NOT count as in-progress — otherwise a config whose optimistic state
    // was rolled back (e.g. a failed build-trigger deletes its liveState key)
    // would be polled forever.
    const s = liveState.value[configId]
    return s !== undefined && GRAPHRAG_IN_PROGRESS.has(s)
  }

  function unwatch(configId: string): void {
    const teardown = watched.get(configId)
    if (teardown) {
      teardown()
      watched.delete(configId)
    }
    // Drop this config's resync generation counter so the map doesn't grow
    // unbounded across configs that are never re-watched. Safe because syncStatus
    // also refuses to apply a resync once a config has left `watched` (below) —
    // so resetting this counter can't let a prior cycle's in-flight resync clobber
    // a re-watched config on a generation collision.
    applySeq.delete(configId)
    // Audit M14: stop the backstop poll once nothing is being watched, so an
    // idle list view isn't left ticking an interval over an empty map forever.
    if (watched.size === 0 && pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function ensurePoll(): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      for (const configId of watched.keys()) {
        if (isInProgress(configId)) void syncStatus(configId)
      }
    }, POLL_FALLBACK_MS)
  }

  function applyState(configId: string, state: GraphragBuildState): void {
    applySeq.set(configId, (applySeq.get(configId) ?? 0) + 1)
    liveState.value = { ...liveState.value, [configId]: state }
    if (!GRAPHRAG_IN_PROGRESS.has(state)) {
      // Terminal: refetch the authoritative config row for both the list and
      // the single-config query (caller-supplied keys), and close this
      // config's channel so subscriptions don't accumulate over many builds.
      // Deferred so we don't tear down a channel from inside its own event
      // handler. A later re-build re-subscribes via watch().
      for (const key of options.invalidateKeysOnTerminal(configId)) {
        qc.invalidateQueries({ queryKey: key })
      }
      void Promise.resolve().then(() => unwatch(configId))
    }
  }

  async function syncStatus(configId: string): Promise<void> {
    const seq = applySeq.get(configId) ?? 0
    try {
      const state = await options.fetchStatus(configId)
      // The config was unwatched (a terminal frame closed its channel) while we
      // awaited — never re-apply a resync to a config that is no longer live, or
      // it strands the row as in-progress with an orphaned channel (B1). This also
      // makes resetting applySeq on unwatch safe against a generation collision.
      if (!watched.has(configId)) return
      // A newer state (a WS frame or another resync) landed while we awaited —
      // drop this now-stale result rather than clobber it (B1).
      if ((applySeq.get(configId) ?? 0) !== seq) return
      applyState(configId, state)
    } catch {
      // Best-effort recovery — live events still drive subsequent updates.
    }
  }

  // `initialState` seeds liveState so the backstop poll can recover a build even
  // when the WebSocket never connects (audit C6). Without it, liveState stayed
  // undefined until a successful connect, and the poll — which skips undefined —
  // could never fire for an offline/degraded socket, the exact case it exists
  // to cover.
  function watch(configId: string, initialState?: GraphragBuildState): void {
    // Seed the known state when there isn't already a live in-progress one, so
    // a fresh in-progress state (e.g. 'running') replaces a stale terminal value
    // left by a previous build in this session — otherwise the poll backstop
    // would skip it (audit review #6). Never clobber an in-progress state.
    const cur = liveState.value[configId]
    if (initialState !== undefined && (cur === undefined || !GRAPHRAG_IN_PROGRESS.has(cur))) {
      liveState.value = { ...liveState.value, [configId]: initialState }
    }
    if (watched.has(configId)) return
    const path = `${options.pathPrefix}/${configId}`
    const channel = wsManager.channel(path)

    const unsubscribeEvent = channel.subscribe('*', (ev: ChannelEvent) => {
      if (ev.type === 'build.state' && typeof ev.state === 'string' && isBuildState(ev.state)) {
        applyState(configId, ev.state)
      }
    })
    const unsubscribeStatus = channel.onStatus((connected) => {
      if (connected) void syncStatus(configId)
    })

    watched.set(configId, () => {
      unsubscribeEvent()
      unsubscribeStatus()
      wsManager.close(path)
    })
    channel.connect()
    ensurePoll()
  }

  onBeforeUnmount(() => {
    if (pollTimer !== null) clearInterval(pollTimer)
    for (const teardown of watched.values()) teardown()
    watched.clear()
  })

  return { liveState, watch, unwatch }
}

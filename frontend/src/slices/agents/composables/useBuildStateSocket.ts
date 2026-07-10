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
import { GRAPHRAG_IN_PROGRESS, type GraphragBuildState } from '../api'

const POLL_FALLBACK_MS = 15000

function isBuildState(s: string): s is GraphragBuildState {
  return GRAPHRAG_IN_PROGRESS.has(s as GraphragBuildState) || ['idle', 'qdrant_committed', 'failed'].includes(s)
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
    try {
      applyState(configId, await options.fetchStatus(configId))
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

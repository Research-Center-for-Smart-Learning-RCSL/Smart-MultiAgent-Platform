// Real-time Knowledge Map build progress (Phase 3β/4β, R11.24).
// Subscribes to /ws/knowmap/{id} for `build.state` events, mirroring
// useGraphragSocket's single-event shape (not useRagConfigSocket's
// multi-stage ingestion protocol — the Knowledge Map WS channel carries only
// build.state, same as Concept Map's, per ws/knowmap.py's docstring).
// Unlike GraphRAG there is no dedicated GET .../status endpoint; the backstop
// re-fetches the config itself and reads last_build_state off it.

import { useQueryClient } from '@tanstack/vue-query'
import { onBeforeUnmount, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { agentKeys } from '../queries'
import { agentsApi, GRAPHRAG_IN_PROGRESS, type GraphragBuildState } from '../api'

// Backstop poll: build.state events are best-effort (a Redis hiccup can drop
// one). Without a fallback a lost terminal event strands the card on
// 'running' while the socket stays connected. Mirrors useGraphragSocket.
const POLL_FALLBACK_MS = 15000

function isBuildState(s: string): s is GraphragBuildState {
  return GRAPHRAG_IN_PROGRESS.has(s as GraphragBuildState) || ['idle', 'qdrant_committed', 'failed'].includes(s)
}

export function useKnowmapSocket(projectId: string) {
  const qc = useQueryClient()
  // configId -> latest live build state.
  const liveState = ref<Record<string, GraphragBuildState>>({})
  // configId -> teardown for its channel subscription.
  const watched = new Map<string, () => void>()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function isInProgress(configId: string): boolean {
    const s = liveState.value[configId]
    return s !== undefined && GRAPHRAG_IN_PROGRESS.has(s)
  }

  function unwatch(configId: string): void {
    const teardown = watched.get(configId)
    if (teardown) {
      teardown()
      watched.delete(configId)
    }
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
      // Terminal: refetch the authoritative config row (last_build_at, state)
      // for both the list and the single-config query, and close this
      // config's channel so subscriptions don't accumulate over many builds.
      qc.invalidateQueries({ queryKey: agentKeys.knowmapConfigs(projectId) })
      qc.invalidateQueries({ queryKey: agentKeys.knowmapConfig(configId) })
      void Promise.resolve().then(() => unwatch(configId))
    }
  }

  async function syncStatus(configId: string): Promise<void> {
    try {
      // No dedicated /status route for Knowledge Map — the config row already
      // inlines last_build_state/last_build_at/last_build_error.
      const { data } = await agentsApi.getKnowmapConfig(configId)
      applyState(configId, data.last_build_state)
    } catch {
      // Best-effort recovery — live events still drive subsequent updates.
    }
  }

  // `initialState` seeds liveState so the backstop poll can recover a build
  // even when the WebSocket never connects. Mirrors useGraphragSocket.
  function watch(configId: string, initialState?: GraphragBuildState): void {
    const cur = liveState.value[configId]
    if (initialState !== undefined && (cur === undefined || !GRAPHRAG_IN_PROGRESS.has(cur))) {
      liveState.value = { ...liveState.value, [configId]: initialState }
    }
    if (watched.has(configId)) return
    const path = `/knowmap/${configId}`
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

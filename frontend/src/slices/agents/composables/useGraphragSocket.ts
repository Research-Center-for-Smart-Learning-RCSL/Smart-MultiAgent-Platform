// Real-time GraphRAG build progress (R11.04).
// Subscribes to /ws/graphrag/{id} for `build.state` events, replacing REST
// polling. The list view can watch several configs at once, so this manages a
// channel per watched config and tears them all down on unmount.
// On (re)connect it refetches status once to recover any state change missed
// while disconnected.

import { useQueryClient } from '@tanstack/vue-query'
import { onBeforeUnmount, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { agentKeys } from '../queries'
import { agentsApi, GRAPHRAG_IN_PROGRESS, type GraphragBuildState } from '../api'

// Backstop poll: build.state events are best-effort (a Redis hiccup can drop
// one). Without a fallback a lost terminal event strands the card on 'running'
// while the socket stays connected. A slow re-sync of in-progress configs
// recovers it. (Primary signal is still the WS event; this is just a safety net.)
const POLL_FALLBACK_MS = 15000

function isBuildState(s: string): s is GraphragBuildState {
  return GRAPHRAG_IN_PROGRESS.has(s as GraphragBuildState) || ['idle', 'qdrant_committed', 'failed'].includes(s)
}

export function useGraphragSocket(projectId: string) {
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
      // Terminal: refetch the authoritative config row (last_build_at, binding)
      // for both the list and the single-config query (audit M13 — the agent
      // detail Knowledge tab reads graphragConfig(id)), and close this config's
      // channel so subscriptions don't accumulate over many builds. Deferred so
      // we don't tear down a channel from inside its own event handler. A later
      // re-build re-subscribes via watch().
      qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
      qc.invalidateQueries({ queryKey: agentKeys.graphragConfig(configId) })
      void Promise.resolve().then(() => unwatch(configId))
    }
  }

  async function syncStatus(configId: string): Promise<void> {
    try {
      const { data } = await agentsApi.getGraphragStatus(configId)
      applyState(configId, data.state)
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
    const path = `/graphrag/${configId}`
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

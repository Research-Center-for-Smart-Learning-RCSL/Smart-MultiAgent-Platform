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
import { agentsApi } from '../api'

// States that mean a build is still moving — anything else is terminal.
const IN_PROGRESS = new Set(['running', 'neo4j_committed', 'failed_compensating'])

// Backstop poll: build.state events are best-effort (a Redis hiccup can drop
// one). Without a fallback a lost terminal event strands the card on 'running'
// while the socket stays connected. A slow re-sync of in-progress configs
// recovers it. (Primary signal is still the WS event; this is just a safety net.)
const POLL_FALLBACK_MS = 15000

export function useGraphragSocket(projectId: string) {
  const qc = useQueryClient()
  // configId -> latest live build state.
  const liveState = ref<Record<string, string>>({})
  // configId -> teardown for its channel subscription.
  const watched = new Map<string, () => void>()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function isInProgress(configId: string): boolean {
    const s = liveState.value[configId]
    // Unknown (just-triggered, no event yet) counts as in-progress.
    return s === undefined || IN_PROGRESS.has(s)
  }

  function ensurePoll(): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      for (const configId of watched.keys()) {
        if (isInProgress(configId)) void syncStatus(configId)
      }
    }, POLL_FALLBACK_MS)
  }

  function applyState(configId: string, state: string): void {
    liveState.value = { ...liveState.value, [configId]: state }
    if (!IN_PROGRESS.has(state)) {
      // Terminal: refetch the authoritative config row (last_build_at, binding).
      qc.invalidateQueries({ queryKey: agentKeys.graphragConfigs(projectId) })
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

  function watch(configId: string): void {
    if (watched.has(configId)) return
    const path = `/graphrag/${configId}`
    const channel = wsManager.channel(path)

    const unsubscribeEvent = channel.subscribe('*', (ev: ChannelEvent) => {
      if (ev.type === 'build.state' && typeof ev.state === 'string') {
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

  return { liveState, watch }
}

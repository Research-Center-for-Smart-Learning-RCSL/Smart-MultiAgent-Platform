import { readonly, ref, type DeepReadonly, type Ref } from 'vue'
import { http } from '@shared/transport'
import { queryClient } from '@shared/query-client'

// Global connection-status singleton (§12 Shared Patterns §4.4).
//
// The transport layer flags a dropped connection via `markConnectionLost()`;
// this module then probes `/healthz` on an exponential backoff (1s, 2s, 4s, 8s
// … capped at 30s) until the server answers, and any successful HTTP response
// (`markConnectionRestored()`, called from the axios success interceptor) clears
// the offline state immediately. On recovery every active query is invalidated
// so the UI catches up on whatever changed while the tab was dark.
//
// State lives at module scope, not inside the composable, so the single
// `SNetworkBanner` and the transport interceptors observe one shared truth.

const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000

const online = ref(true)
const reconnecting = ref(false)

let backoff = INITIAL_BACKOFF_MS
let probeTimer: ReturnType<typeof setTimeout> | undefined
let probeInFlight = false

function clearProbeTimer(): void {
  if (probeTimer !== undefined) {
    clearTimeout(probeTimer)
    probeTimer = undefined
  }
}

function scheduleProbe(delay: number): void {
  clearProbeTimer()
  probeTimer = setTimeout(() => {
    probeTimer = undefined
    void probe()
  }, delay)
}

async function probe(): Promise<void> {
  // A real request may have already cleared the offline state; nothing to do.
  if (online.value || probeInFlight) return
  probeInFlight = true
  reconnecting.value = true
  try {
    // `/healthz` is the root-mounted liveness endpoint (no `/api` prefix), so
    // override the instance baseURL. It still rides the shared interceptors —
    // an answered probe also clears the offline state through the success path.
    await http.get('/healthz', { baseURL: '' })
    markConnectionRestored()
  } catch {
    // Still unreachable — back off and try again. Doubling is capped so a long
    // outage settles into a steady 30s poll rather than minutes-long gaps.
    if (!online.value) {
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS)
      scheduleProbe(backoff)
    }
  } finally {
    probeInFlight = false
  }
}

/** Flag the connection as lost and start the recovery probe loop. Idempotent —
 *  repeated calls while already offline do not restart the backoff. */
export function markConnectionLost(): void {
  if (!online.value) return
  online.value = false
  reconnecting.value = true
  backoff = INITIAL_BACKOFF_MS
  scheduleProbe(backoff)
}

/** Flag the connection as restored. Safe to call on every successful response;
 *  it only invalidates queries on a genuine offline→online transition. */
export function markConnectionRestored(): void {
  const wasOffline = !online.value
  online.value = true
  reconnecting.value = false
  backoff = INITIAL_BACKOFF_MS
  clearProbeTimer()
  if (wasOffline) {
    void queryClient.invalidateQueries()
  }
}

/** Force an immediate recovery probe, bypassing the backoff wait. Bound to the
 *  "Retry Now" affordance and the browser `online` event. */
export function retryNow(): void {
  if (online.value) return
  clearProbeTimer()
  void probe()
}

// Browser hints: `offline` is authoritative enough to flip the banner on, but
// `online` only means the NIC is up — confirm reachability with a probe rather
// than trusting it outright.
if (typeof window !== 'undefined') {
  window.addEventListener('offline', markConnectionLost)
  window.addEventListener('online', retryNow)
}

export interface NetworkStatus {
  online: DeepReadonly<Ref<boolean>>
  reconnecting: DeepReadonly<Ref<boolean>>
  retryNow: () => void
}

export function useNetworkStatus(): NetworkStatus {
  return {
    online: readonly(online),
    reconnecting: readonly(reconnecting),
    retryNow,
  }
}

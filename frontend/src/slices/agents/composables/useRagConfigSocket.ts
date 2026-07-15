// Real-time RAG config ingestion progress (R24.22, R24.23).
//
// Subscribes to /ws/rag-configs/{id} for ingestion events, but treats the
// authoritative per-document REST state as the source of truth: on connect,
// reconnect, and mount it re-derives `progress` from listDocuments (F-21/F-28),
// so a missed terminal event or a late subscribe can never leave the bar stale.
// Mirrors useBuildStateSocket's resync-on-connect + backstop-poll recovery.
//
// The live ingestion.* frames are chunk-granular for a SINGLE document
// (ingestion.progress carries a chunk total; started/completed/failed carry a
// document_id), so they must never write the corpus-level document counts.
// syncState() is the ONLY writer of progress; the frames are change-triggers.

import { useQueryClient } from '@tanstack/vue-query'
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { agentKeys } from '../queries'
import { agentsApi, type RagDocument } from '../api'

const POLL_FALLBACK_MS = 15000
const SYNC_DEBOUNCE_MS = 750
// Surfaced when a resync derives `failed` from document state alone. The bar
// itself does not render this string (it hides on a terminal state); it exists
// so a consumer can distinguish a failed corpus from a clean one.
const GENERIC_INGEST_ERROR = 'One or more documents failed to ingest'

export interface RagIngestionProgress {
  state: 'idle' | 'ingesting' | 'indexing' | 'ready' | 'failed'
  documentsTotal: number
  documentsProcessed: number
  error: string | null
}

const IN_PROGRESS: ReadonlySet<RagIngestionProgress['state']> = new Set(['ingesting', 'indexing'])

export function useRagConfigSocket(configId: string, projectId: string) {
  const qc = useQueryClient()
  const connected = ref(false)
  const progress = ref<RagIngestionProgress>({
    state: 'idle',
    documentsTotal: 0,
    documentsProcessed: 0,
    error: null,
  })

  const path = `/rag-configs/${configId}`
  const channel = wsManager.channel(path)

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  // Monotonic id for each resync. Only the most recently issued syncState is
  // allowed to apply its result, so a slower earlier-issued listDocuments
  // response that resolves late can never overwrite the newer authoritative
  // state (B1: a stale resync stranding the bar on an outdated snapshot).
  let syncSeq = 0

  // Derive corpus-level progress from the authoritative per-document status
  // list. Document status has no `indexing` sub-state, so the finer live
  // `indexing` state is preserved over a derived `ingesting` (never a downgrade
  // of an in-progress build); a terminal or idle derivation always wins.
  function deriveProgress(docs: RagDocument[]): RagIngestionProgress {
    const documentsTotal = docs.length
    const anyIngesting = docs.some((d) => d.status === 'ingesting')
    const anyFailed = docs.some((d) => d.status === 'failed')
    const documentsProcessed = docs.filter((d) => d.status !== 'ingesting').length
    let state: RagIngestionProgress['state']
    if (anyIngesting) {
      state = progress.value.state === 'indexing' ? 'indexing' : 'ingesting'
    } else if (anyFailed) {
      state = 'failed'
    } else if (documentsTotal > 0) {
      state = 'ready'
    } else {
      state = 'idle'
    }
    return {
      state,
      documentsTotal,
      documentsProcessed,
      error: state === 'failed' ? GENERIC_INGEST_ERROR : null,
    }
  }

  async function syncState(): Promise<void> {
    const seq = ++syncSeq
    try {
      const docs = await agentsApi.listDocuments(configId)
      // A newer resync was issued while this listDocuments awaited — drop this
      // now-stale snapshot rather than let it overwrite the fresher state (B1).
      if (seq !== syncSeq) return
      progress.value = deriveProgress(docs)
      qc.invalidateQueries({ queryKey: agentKeys.ragDocuments(configId) })
      qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
      if (IN_PROGRESS.has(progress.value.state)) startPoll()
      else stopPoll()
    } catch {
      // Best-effort recovery — live events still drive subsequent updates.
    }
  }

  function scheduleSync(): void {
    if (debounceTimer !== null) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      debounceTimer = null
      void syncState()
    }, SYNC_DEBOUNCE_MS)
  }

  // A 15s backstop so a dropped terminal frame still self-heals without a
  // reload; only runs while in-progress and stops itself at terminal/idle.
  function startPoll(): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      if (IN_PROGRESS.has(progress.value.state)) void syncState()
      else stopPoll()
    }, POLL_FALLBACK_MS)
  }

  function stopPoll(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'ingestion.started':
        // Instant "something started" hint; the debounced resync reconciles
        // counts and the corpus-level state authoritatively.
        if (progress.value.state === 'idle') progress.value.state = 'ingesting'
        scheduleSync()
        break
      case 'ingestion.indexing':
        // `indexing` exists only as a live signal (no document sub-state);
        // the resync preserves it until a terminal derivation.
        progress.value.state = 'indexing'
        scheduleSync()
        break
      case 'ingestion.progress':
        // Late-subscriber feedback (F-28): show the bar immediately, but take
        // the counts from the debounced authoritative resync, never from this
        // chunk-granular frame (F-28 unit bug).
        if (progress.value.state === 'idle') progress.value.state = 'ingesting'
        scheduleSync()
        break
      case 'ingestion.completed':
      case 'ingestion.failed':
        // Per-document terminal frames: whether the CORPUS is done is decided
        // by authoritative document state, not this one document's event, so
        // no optimistic terminal flip here (it would wrongly flip the bar
        // mid-upload). The debounced resync derives ready/failed/still-ingesting.
        scheduleSync()
        break
    }
    // All ingestion.* frames are per-document and chunk-granular, and arrive in
    // bursts across a multi-file upload; routing every frame through the same
    // debounced syncState coalesces the burst into ~1 authoritative document
    // refetch per window instead of one fetch + two query invalidations per
    // frame. Connect/reconnect/mount resyncs stay immediate (below).
  }

  const unsubscribeEvent = channel.subscribe('*', handleEvent)
  const unsubscribeStatus = channel.onStatus((isConnected) => {
    connected.value = isConnected
    if (isConnected) void syncState()
  })

  onMounted(() => {
    // Seed authoritative state even before the socket connects (mirrors
    // useBuildStateSocket's initialState seeding).
    void syncState()
    channel.connect()
  })

  onActivated(() => {
    void syncState()
    channel.connect()
  })

  onDeactivated(() => {
    channel.disconnect()
    stopPoll()
    // Cancel any pending debounced resync too — otherwise a frame that landed
    // just before deactivation could fire syncState afterwards and restart the
    // poll on a cached (kept-alive) off-screen view.
    if (debounceTimer !== null) clearTimeout(debounceTimer)
  })

  onBeforeUnmount(() => {
    unsubscribeEvent()
    unsubscribeStatus()
    stopPoll()
    if (debounceTimer !== null) clearTimeout(debounceTimer)
    wsManager.close(path)
  })

  return { connected, progress }
}

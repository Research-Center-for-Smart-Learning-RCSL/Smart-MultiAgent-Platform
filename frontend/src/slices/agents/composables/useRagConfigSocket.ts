// Real-time RAG config ingestion progress (R24.22).
// Subscribes to /ws/rag-configs/{id} for ingestion events.
// On reconnect, fetches current status to recover any missed updates.

import { useQueryClient } from '@tanstack/vue-query'
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { agentKeys } from '../queries'
import { agentsApi } from '../api'

export interface RagIngestionProgress {
  state: 'idle' | 'ingesting' | 'indexing' | 'ready' | 'failed'
  documentsTotal: number
  documentsProcessed: number
  error: string | null
}

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

  function handleEvent(ev: ChannelEvent): void {
    switch (ev.type) {
      case 'ingestion.started':
        progress.value = {
          state: 'ingesting',
          documentsTotal: (ev.total as number) ?? 0,
          documentsProcessed: 0,
          error: null,
        }
        break
      case 'ingestion.progress':
        progress.value.documentsProcessed = (ev.processed as number) ?? 0
        break
      case 'ingestion.indexing':
        progress.value.state = 'indexing'
        break
      case 'ingestion.completed':
        progress.value.state = 'ready'
        progress.value.documentsProcessed = progress.value.documentsTotal
        qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
        break
      case 'ingestion.failed':
        progress.value.state = 'failed'
        progress.value.error = (ev.error as string) ?? 'Unknown error'
        qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
        break
    }
  }

  async function syncOnReconnect(): Promise<void> {
    try {
      const { data } = await agentsApi.listRagConfigs(projectId)
      const current = data.find((c) => c.id === configId)
      if (current) {
        qc.invalidateQueries({ queryKey: agentKeys.ragConfigs(projectId) })
      }
    } catch {
      // Non-fatal — UI will show stale progress until next event.
    }
  }

  channel.subscribe('*', handleEvent)
  channel.onStatus((isConnected) => {
    connected.value = isConnected
    if (isConnected) void syncOnReconnect()
  })

  onMounted(() => {
    channel.connect()
  })

  onActivated(() => {
    channel.connect()
  })

  onDeactivated(() => {
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    wsManager.close(path)
  })

  return { connected, progress }
}

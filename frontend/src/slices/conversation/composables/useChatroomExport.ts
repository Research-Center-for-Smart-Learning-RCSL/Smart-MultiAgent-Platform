// Composable: chatroom export job creation and status polling.
// Extracted from ChatroomView.vue (C4 SoC fix).

import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { usePolling } from '@shared/composables'
import { createExport, getExport, type ExportOptions } from '../api'
import type { ExportStatus } from '../types'

export function useChatroomExport(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()

  const EXPORT_TERMINAL = new Set<ExportStatus['status']>(['ready', 'failed'])
  const exportJob = ref<Pick<ExportStatus, 'status' | 'url'> | null>(null)
  // The single-slot consumer: `exportJob` holds one job at a time, so the
  // poller must feed it from exactly one key. Track which key owns the slot and
  // ignore ticks from any superseded job.
  const activeJobId = ref<string | null>(null)

  const exportPoll = usePolling<ExportStatus>((jobId) => getExport(jobId), {
    maxAttempts: 60,
    isTerminal: (s) => EXPORT_TERMINAL.has(s.status),
    onResult: (jobId, s) => {
      if (jobId !== activeJobId.value) return
      exportJob.value = { status: s.status, url: s.url }
    },
  })

  async function runExport(opts: ExportOptions = {}): Promise<void> {
    // Supersede any earlier job before starting a new one, so its poller stops
    // and cannot write into the slot the new job now owns.
    if (activeJobId.value) exportPoll.cancel(activeJobId.value)
    try {
      const { job_id, status } = await createExport(chatroomId, opts)
      activeJobId.value = job_id
      exportJob.value = { status: status as ExportStatus['status'], url: null }
      exportPoll.start(job_id)
    } catch {
      activeJobId.value = null
      exportJob.value = { status: 'failed', url: null }
      toast.error(t('conversation.chatroom.exportFailed'))
    }
  }

  function reset(): void {
    if (activeJobId.value) exportPoll.cancel(activeJobId.value)
    activeJobId.value = null
    exportJob.value = null
  }

  return {
    exportJob,
    runExport,
    reset,
  }
}

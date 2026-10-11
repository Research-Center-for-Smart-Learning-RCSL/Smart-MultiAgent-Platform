// Composable: chatroom export job creation and status polling.
// Extracted from ChatroomView.vue (C4 SoC fix).

import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { usePolling } from '@shared/composables'
import { createExport, getExport, type ExportOptions } from '../api'
import type { ExportStatus } from '../types'
import { ApiError } from '@shared/errors'
import { triggerDownload } from '@shared/utils/download'

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

  // The link in `exportJob` was minted when the job became ready and expires
  // minutes later; fetch a fresh one at click time. The archive itself lives
  // 24 h, after which the job is gone (404) or its object is (410 on download).
  async function download(): Promise<void> {
    const jobId = activeJobId.value
    if (!jobId) return
    try {
      const fresh = await getExport(jobId)
      if (fresh.status === 'ready' && fresh.url) {
        triggerDownload(fresh.url)
        return
      }
      toast.error(t('conversation.chatroom.exportExpired'))
    } catch (err) {
      const gone = err instanceof ApiError && (err.status === 404 || err.status === 410)
      toast.error(t(gone ? 'conversation.chatroom.exportExpired' : 'conversation.chatroom.exportDownloadFailed'))
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
    download,
    reset,
  }
}

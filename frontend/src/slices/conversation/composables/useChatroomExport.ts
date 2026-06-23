// Composable: chatroom export job creation and status polling.
// Extracted from ChatroomView.vue (C4 SoC fix).

import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { usePolling } from '@shared/composables'
import { createExport, getExport } from '../api'
import type { ExportStatus } from '../types'

export function useChatroomExport(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()

  const EXPORT_TERMINAL = new Set<ExportStatus['status']>(['ready', 'failed'])
  const exportJob = ref<Pick<ExportStatus, 'status' | 'url'> | null>(null)

  const exportPoll = usePolling<ExportStatus>((jobId) => getExport(jobId), {
    maxAttempts: 60,
    isTerminal: (s) => EXPORT_TERMINAL.has(s.status),
    onResult: (_jobId, s) => {
      exportJob.value = { status: s.status, url: s.url }
    },
  })

  async function runExport(): Promise<void> {
    try {
      const { job_id, status } = await createExport(chatroomId)
      exportJob.value = { status: status as ExportStatus['status'], url: null }
      exportPoll.start(job_id)
    } catch {
      exportJob.value = { status: 'failed', url: null }
      toast.error(t('conversation.chatroom.exportFailed'))
    }
  }

  return {
    exportJob,
    runExport,
  }
}

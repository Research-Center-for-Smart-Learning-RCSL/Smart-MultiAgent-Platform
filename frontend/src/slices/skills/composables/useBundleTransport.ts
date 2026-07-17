import { computed, onScopeDispose, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQueryClient } from '@tanstack/vue-query'

import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'

import {
  skillsKeys,
  useExportBundleMutation,
  useExportStatusQuery,
  useImportBundleMutation,
  useImportStatusQuery,
} from '../queries'
import type { SkillScopeRef } from '../types'

/**
 * Drives bundle import and export for one scope: enqueue over multipart, then poll the
 * scope-neutral job endpoint until it reaches a terminal state, reacting once per job.
 * On a ready import the scope's skill list is invalidated so the new skill appears; on a
 * ready export the presigned URL is opened to download the .zip.
 */
export function useBundleTransport(scope: SkillScopeRef) {
  const { t } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()

  const importMutation = useImportBundleMutation(scope)
  const exportMutation = useExportBundleMutation(scope)

  const importTaskId = ref<string | null>(null)
  const exportTaskId = ref<string | null>(null)

  const importStatus = useImportStatusQuery(importTaskId)
  const exportStatus = useExportStatusQuery(exportTaskId)

  // A job that never reaches a terminal state (a crashed worker) would otherwise poll
  // forever with the spinner stuck on; a status endpoint that keeps erroring (a 500, or a
  // 403 for a non-initiator) would give no feedback at all. Bound both with a wall-clock
  // timeout that stops the poll and surfaces the give-up.
  const POLL_TIMEOUT_MS = 90_000
  let importTimer: ReturnType<typeof setTimeout> | null = null
  let exportTimer: ReturnType<typeof setTimeout> | null = null

  function clearImportTimer(): void {
    if (importTimer) {
      clearTimeout(importTimer)
      importTimer = null
    }
  }
  function clearExportTimer(): void {
    if (exportTimer) {
      clearTimeout(exportTimer)
      exportTimer = null
    }
  }
  onScopeDispose(() => {
    clearImportTimer()
    clearExportTimer()
  })

  const importing = computed(
    () => importMutation.isPending.value || importStatus.data.value?.status === 'queued' || importStatus.data.value?.status === 'running',
  )
  const exporting = computed(
    () => exportMutation.isPending.value || exportStatus.data.value?.status === 'queued' || exportStatus.data.value?.status === 'running',
  )

  // Each terminal transition is handled once: the poll keeps returning the same terminal
  // row, so a guard on the job id stops a repeated toast.
  const handledImport = ref<string | null>(null)
  watch(
    () => importStatus.data.value,
    (state) => {
      if (!state || state.job_id === handledImport.value) return
      if (state.status === 'ready') {
        handledImport.value = state.job_id
        clearImportTimer()
        qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) })
        toast.success(
          t('skills.bundle.importReady'),
          state.warnings.length ? { description: state.warnings.join('\n') } : undefined,
        )
      } else if (state.status === 'failed') {
        handledImport.value = state.job_id
        clearImportTimer()
        toast.error(t('skills.bundle.importFailed'), state.error ? { description: state.error } : undefined)
      }
    },
  )

  const handledExport = ref<string | null>(null)
  watch(
    () => exportStatus.data.value,
    (state) => {
      if (!state || state.job_id === handledExport.value) return
      if (state.status === 'ready') {
        handledExport.value = state.job_id
        clearExportTimer()
        // Ready always carries a URL (the worker sets bucket+key with the READY status);
        // guard anyway so a URL-less ready surfaces as a failure rather than a silent no-op.
        if (state.url) {
          triggerDownload(state.url)
          toast.success(t('skills.bundle.exportReady'))
        } else {
          toast.error(t('skills.bundle.exportFailed'))
        }
      } else if (state.status === 'failed') {
        handledExport.value = state.job_id
        clearExportTimer()
        toast.error(t('skills.bundle.exportFailed'), state.error ? { description: state.error } : undefined)
      }
    },
  )

  function triggerDownload(url: string): void {
    // The presigned URL carries a content-disposition attachment header, so navigating to
    // it downloads the .zip rather than replacing the page. The anchor must be attached to
    // the document before click() — Firefox ignores a click on a detached anchor.
    const a = document.createElement('a')
    a.href = url
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  async function importFile(file: File): Promise<void> {
    try {
      const job = await importMutation.mutateAsync(file)
      const jobId = job.job_id
      handledImport.value = null
      clearImportTimer()
      importTaskId.value = jobId
      importTimer = setTimeout(() => {
        importTimer = null
        if (handledImport.value === jobId) return
        handledImport.value = jobId
        importTaskId.value = null // disable the query so polling stops
        toast.error(t('skills.bundle.importTimeout'))
      }, POLL_TIMEOUT_MS)
    } catch (err) {
      if (isProblemWithType(err, 'skills/bundle-invalid')) {
        toast.error(t('skills.bundle.invalid'))
      } else if (isProblemWithType(err, 'skills/bundle-quarantined')) {
        toast.error(t('skills.bundle.quarantined'))
      } else {
        toast.error(t('skills.bundle.importFailed'))
      }
    }
  }

  async function exportSkill(skillId: string): Promise<void> {
    try {
      const job = await exportMutation.mutateAsync(skillId)
      const jobId = job.job_id
      handledExport.value = null
      clearExportTimer()
      exportTaskId.value = jobId
      exportTimer = setTimeout(() => {
        exportTimer = null
        if (handledExport.value === jobId) return
        handledExport.value = jobId
        exportTaskId.value = null // disable the query so polling stops
        toast.error(t('skills.bundle.exportTimeout'))
      }, POLL_TIMEOUT_MS)
    } catch {
      toast.error(t('skills.bundle.exportFailed'))
    }
  }

  return { importFile, exportSkill, importing, exporting, importStatus, exportStatus }
}

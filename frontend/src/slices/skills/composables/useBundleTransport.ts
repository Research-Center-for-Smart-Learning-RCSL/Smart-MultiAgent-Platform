import { computed, ref, watch } from 'vue'
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
        qc.invalidateQueries({ queryKey: skillsKeys.listRoot(scope) })
        toast.success(
          t('skills.bundle.importReady'),
          state.warnings.length ? { description: state.warnings.join('\n') } : undefined,
        )
      } else if (state.status === 'failed') {
        handledImport.value = state.job_id
        toast.error(t('skills.bundle.importFailed'), state.error ? { description: state.error } : undefined)
      }
    },
  )

  const handledExport = ref<string | null>(null)
  watch(
    () => exportStatus.data.value,
    (state) => {
      if (!state || state.job_id === handledExport.value) return
      if (state.status === 'ready' && state.url) {
        handledExport.value = state.job_id
        triggerDownload(state.url)
        toast.success(t('skills.bundle.exportReady'))
      } else if (state.status === 'failed') {
        handledExport.value = state.job_id
        toast.error(t('skills.bundle.exportFailed'), state.error ? { description: state.error } : undefined)
      }
    },
  )

  function triggerDownload(url: string): void {
    // The presigned URL carries a content-disposition attachment header, so navigating to
    // it downloads the .zip rather than replacing the page.
    const a = document.createElement('a')
    a.href = url
    a.rel = 'noopener'
    a.click()
  }

  async function importFile(file: File): Promise<void> {
    try {
      const job = await importMutation.mutateAsync(file)
      handledImport.value = null
      importTaskId.value = job.job_id
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
      handledExport.value = null
      exportTaskId.value = job.job_id
    } catch {
      toast.error(t('skills.bundle.exportFailed'))
    }
  }

  return { importFile, exportSkill, importing, exporting, importStatus, exportStatus }
}

import { computed, type MaybeRefOrGetter, ref, toValue } from 'vue'
import { useI18n } from 'vue-i18n'

import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'

import {
  useCreateFileMutation,
  useDeleteFileMutation,
  usePatchFileMutation,
  useSkillFilesQuery,
  useUploadFileMutation,
} from '../queries'
import type { SkillFileOut, SkillScopeRef } from '../types'

// A file whose top-level directory is `assets/` is an opaque binary: not editable as
// text (AC-17). Derived here for the editor gate; the backend derives the same `kind`
// from the path and rejects a PATCH to an asset with `skills/file-not-editable`.
export function isAssetFile(file: SkillFileOut): boolean {
  return file.kind === 'asset'
}

/**
 * Files for one skill, plus its whole-skill readability.
 *
 * The backend exposes no HTTP endpoint that returns a file's stored bytes (only the
 * model-facing `read_skill` tool and the sandbox stager read them), so the editor cannot
 * pre-load existing content. Content the user authors or replaces *this session* is kept
 * in `authoredContent` so re-opening it shows what was written; a file from a prior
 * session or an upload opens blank, and saving replaces its content (which changes its
 * sha256 — AC-16). See the slice README note / dossier D-72.
 */
export function useSkillFiles(scope: SkillScopeRef, skillId: MaybeRefOrGetter<string | null>) {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()

  const filesQuery = useSkillFilesQuery(scope, skillId)
  const createMutation = useCreateFileMutation(scope)
  const uploadMutation = useUploadFileMutation(scope)
  const patchMutation = usePatchFileMutation(scope)
  const deleteMutation = useDeleteFileMutation(scope)

  const files = computed<SkillFileOut[]>(() => filesQuery.data.value ?? [])

  // Fail-closed like the backend gate: only `clean` serves. Anything else (pending during
  // a scan, infected, or scan error) makes the whole skill unreadable by agents (AC-34).
  const unreadableFiles = computed(() => files.value.filter((f) => f.scan_status !== 'clean'))
  const pendingFiles = computed(() => files.value.filter((f) => f.scan_status === 'pending'))
  const quarantinedFiles = computed(() =>
    files.value.filter((f) => f.scan_status === 'infected' || f.scan_status === 'error'),
  )
  const readable = computed(() => files.value.length === 0 || unreadableFiles.value.length === 0)

  const authoredContent = ref<Record<string, string>>({})
  const busy = computed(
    () =>
      createMutation.isPending.value ||
      uploadMutation.isPending.value ||
      patchMutation.isPending.value ||
      deleteMutation.isPending.value,
  )

  function pathError(err: unknown): string {
    if (isProblemWithType(err, 'skills/file-path-taken')) return t('skills.files.pathTaken')
    if (isProblemWithType(err, 'skills/bundle-invalid')) return t('skills.files.pathInvalid')
    if (isProblemWithType(err, 'skills/file-limit-exceeded')) return t('skills.files.limit')
    return t('skills.files.addFailed')
  }

  async function addAuthored(path: string, content: string): Promise<boolean> {
    const id = toValue(skillId)
    if (!id) return false
    try {
      const created = await createMutation.mutateAsync({ skillId: id, body: { path, content } })
      authoredContent.value[created.id] = content
      toast.success(t('skills.files.added'))
      return true
    } catch (err) {
      toast.error(pathError(err))
      return false
    }
  }

  async function upload(path: string, file: File): Promise<boolean> {
    const id = toValue(skillId)
    if (!id) return false
    try {
      await uploadMutation.mutateAsync({ skillId: id, path, file })
      toast.success(t('skills.files.uploaded'))
      return true
    } catch (err) {
      toast.error(pathError(err))
      return false
    }
  }

  async function replaceContent(file: SkillFileOut, content: string): Promise<boolean> {
    const id = toValue(skillId)
    if (!id) return false
    try {
      await patchMutation.mutateAsync({ skillId: id, fileId: file.id, body: { content } })
      authoredContent.value[file.id] = content
      toast.success(t('skills.files.replaced'))
      return true
    } catch (err) {
      if (isProblemWithType(err, 'skills/file-not-editable')) {
        toast.error(t('skills.files.notEditable'))
      } else {
        toast.error(t('skills.files.replaceFailed'))
      }
      return false
    }
  }

  async function remove(file: SkillFileOut): Promise<boolean> {
    const id = toValue(skillId)
    if (!id) return false
    const ok = await confirm({
      title: t('skills.files.deleteTitle'),
      message: t('skills.files.deleteBody', { path: file.path }),
      variant: 'error',
    })
    if (!ok) return false
    try {
      await deleteMutation.mutateAsync({ skillId: id, fileId: file.id })
      delete authoredContent.value[file.id]
      toast.success(t('skills.files.deleted'))
      return true
    } catch {
      toast.error(t('skills.files.deleteFailed'))
      return false
    }
  }

  return {
    filesQuery,
    files,
    readable,
    unreadableFiles,
    pendingFiles,
    quarantinedFiles,
    authoredContent,
    busy,
    addAuthored,
    upload,
    replaceContent,
    remove,
  }
}

// Composable: chat attachment uploads (single-shot ≤32MB / tus >32MB, 1GB max
// — matches the backend limits). Extracted from useChatroomMessages so the
// composer's compose+send surface stays focused (ISP).

import { ref, toValue, type MaybeRefOrGetter } from 'vue'

import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { tusUpload, resourceToAttachmentId } from '@shared/transport'

export type UploadStatus = 'uploading' | 'ready' | 'error'

export interface PendingUpload {
  id: string
  filename: string
  progress: number
  attachmentId: string | null
  status: UploadStatus
}

export function useChatroomAttachments(
  chatroomId: string,
  // The chatroom route carries no projectId, so this is resolved reactively
  // (room -> workspace -> project) and read at upload time, not at setup.
  projectId: MaybeRefOrGetter<string | undefined>,
) {
  const { t } = useI18n()
  const toast = useToast()
  const pendingUploads = ref<PendingUpload[]>([])

  // Update one record immutably (replace the array element) instead of mutating
  // a pushed object in place: Vue does not track property writes to a raw object
  // that was pushed into the ref array, so an in-place `record.status = 'ready'`
  // updates the live value but never invalidates computeds that read it (e.g.
  // the composer's `canSend`), leaving the send button wedged off. Reassigning
  // the array triggers reactivity for both the chip and those computeds.
  function patchUpload(id: string, patch: Partial<PendingUpload>): void {
    pendingUploads.value = pendingUploads.value.map((u) => (u.id === id ? { ...u, ...patch } : u))
  }

  async function uploadFiles(files: File[]): Promise<void> {
    const resolvedProjectId = toValue(projectId)
    if (!resolvedProjectId) {
      // Sending an empty project_id makes the tus-create 400; surface a
      // transient error instead so the user retries once the room has loaded.
      toast.error(t('conversation.chatroom.uploadNotReady'))
      return
    }
    for (const file of files) {
      const uploadId = crypto.randomUUID()
      pendingUploads.value = [
        ...pendingUploads.value,
        {
          id: uploadId,
          filename: file.name,
          progress: 0,
          attachmentId: null,
          status: 'uploading',
        },
      ]
      try {
        const result = await tusUpload({
          file,
          purpose: 'chat_attachment',
          projectId: resolvedProjectId,
          chatroomId,
          onProgress: (done, total) => {
            patchUpload(uploadId, { progress: total === 0 ? 1 : done / total })
          },
        })
        const id = resourceToAttachmentId(result.resourceHeader)
        if (!id) {
          // Upload streamed but the server never returned a resource handle:
          // treat as failed rather than silently dropping the attachment at
          // send time (which is what an unresolved id would do).
          throw new Error('missing X-SMAP-Resource')
        }
        patchUpload(uploadId, { attachmentId: id, status: 'ready' })
      } catch {
        patchUpload(uploadId, { status: 'error' })
        toast.error(t('conversation.chatroom.uploadFailed', { filename: file.name }))
      }
    }
  }

  async function onDrop(ev: DragEvent): Promise<void> {
    await uploadFiles(Array.from(ev.dataTransfer?.files ?? []))
  }

  function removeUpload(id: string): void {
    pendingUploads.value = pendingUploads.value.filter((p) => p.id !== id)
  }

  /** Resolved attachment ids ready to attach to an outgoing message. */
  function attachmentIds(): string[] {
    return pendingUploads.value.map((p) => p.attachmentId).filter((x): x is string => !!x)
  }

  function clear(): void {
    pendingUploads.value = []
  }

  return { pendingUploads, uploadFiles, onDrop, removeUpload, attachmentIds, clear }
}

// Composable: chat attachment uploads (single-shot ≤32MB / tus >32MB, 1GB max
// — matches the backend limits). Extracted from useChatroomMessages so the
// composer's compose+send surface stays focused (ISP).

import { ref } from 'vue'

import { useI18n } from 'vue-i18n'
import { tusUpload, resourceToAttachmentId } from '@shared/transport'

export interface PendingUpload {
  id: string
  filename: string
  progress: number
  attachmentId: string | null
}

export function useChatroomAttachments(chatroomId: string, projectId: string) {
  const { t } = useI18n()
  const pendingUploads = ref<PendingUpload[]>([])

  async function uploadFiles(files: File[]): Promise<void> {
    for (const file of files) {
      const record: PendingUpload = {
        id: crypto.randomUUID(),
        filename: file.name,
        progress: 0,
        attachmentId: null,
      }
      pendingUploads.value.push(record)
      try {
        const result = await tusUpload({
          file,
          purpose: 'chat_attachment',
          projectId,
          chatroomId,
          onProgress: (done, total) => {
            record.progress = total === 0 ? 1 : done / total
          },
        })
        record.attachmentId = resourceToAttachmentId(result.resourceHeader)
      } catch {
        record.filename = t('conversation.chatroom.uploadFailed', { filename: record.filename })
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

// Composable: inline message editing (R13.21). Extracted from
// useChatroomMessages to keep edit state out of the broader message surface.

import { useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { editMessage as apiEditMessage } from '../api'
import { convKeys } from '../queries'
import type { Message } from '../types'

export function useChatroomMessageEditing(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()

  const editingId = ref<string | null>(null)
  const editDraft = ref('')
  const editVersion = ref(0)

  function startEdit(m: Message): void {
    editingId.value = m.id
    editDraft.value = m.content_md
    editVersion.value = m.version
  }

  function cancelEdit(): void {
    editingId.value = null
    editDraft.value = ''
  }

  async function saveEdit(): Promise<void> {
    const id = editingId.value
    const text = editDraft.value.trim()
    if (!id || !text) return
    const version = editVersion.value
    const key = convKeys.messages(chatroomId)
    const prevRecent = qc.getQueryData<Message[]>(key)
    // Optimistic content swap in the cache, then close the editor immediately
    // (§7.2). Messages only in the older-pagination pane aren't in this cache;
    // those reconcile via the message.updated WS event instead.
    qc.setQueryData<Message[]>(key, (prev) =>
      prev?.map((m) =>
        m.id === id ? { ...m, content_md: text, edited_at: new Date().toISOString() } : m,
      ),
    )
    cancelEdit()
    try {
      const updated = await apiEditMessage(id, version, text)
      qc.setQueryData<Message[]>(key, (prev) =>
        prev?.map((m) => (m.id === updated.id ? updated : m)),
      )
    } catch {
      // Rollback to the pre-edit content and surface the failure.
      if (prevRecent) qc.setQueryData(key, prevRecent)
      toast.error(t('conversation.chatroom.editFailed'))
    }
  }

  return { editingId, editDraft, startEdit, cancelEdit, saveEdit }
}

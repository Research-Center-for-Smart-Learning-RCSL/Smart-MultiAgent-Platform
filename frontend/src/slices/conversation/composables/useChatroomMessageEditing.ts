// Composable: inline message editing (R13.21). Extracted from
// useChatroomMessages to keep edit state out of the broader message surface.

import { useQueryClient } from '@tanstack/vue-query'
import { ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { editMessage as apiEditMessage, getMessage } from '../api'
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
      // FIX-09: rollback the optimistic content, reopen the editor, and
      // refresh the version from the server so a 412 conflict doesn't loop.
      const original = prevRecent?.find((m) => m.id === id)
      if (original) {
        qc.setQueryData<Message[]>(key, (prev) =>
          prev?.map((m) => (m.id === id ? original : m)),
        )
      }
      editingId.value = id
      editDraft.value = text
      try {
        const fresh = await getMessage(id)
        editVersion.value = fresh.version
        qc.setQueryData<Message[]>(key, (prev) =>
          prev?.map((m) => (m.id === fresh.id ? fresh : m)),
        )
      } catch {
        editVersion.value = version
      }
      toast.error(t('conversation.chatroom.editFailed'))
    }
  }

  return { editingId, editDraft, startEdit, cancelEdit, saveEdit }
}

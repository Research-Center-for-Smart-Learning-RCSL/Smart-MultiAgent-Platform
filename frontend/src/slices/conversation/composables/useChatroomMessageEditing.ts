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
    try {
      await apiEditMessage(id, editVersion.value, text)
      cancelEdit()
      await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
    } catch {
      toast.error(t('conversation.chatroom.editFailed'))
    }
  }

  return { editingId, editDraft, startEdit, cancelEdit, saveEdit }
}

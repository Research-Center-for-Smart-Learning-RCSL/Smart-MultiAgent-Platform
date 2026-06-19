// Composable: chatroom message search state and execution.
// Extracted from ChatroomView.vue (C4 SoC fix).

import { computed, ref } from 'vue'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { searchMessages } from '../api'
import { sanitizeSnippet } from '../lib/renderMarkdown'
import type { SearchHit } from '../types'

export function useChatroomSearch(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()

  const searchQuery = ref('')
  const searchHits = ref<SearchHit[]>([])

  const renderedSnippets = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const h of searchHits.value) out[h.message_id] = sanitizeSnippet(h.snippet)
    return out
  })

  async function runSearch(): Promise<void> {
    if (!searchQuery.value.trim()) {
      searchHits.value = []
      return
    }
    try {
      const res = await searchMessages(chatroomId, searchQuery.value.trim())
      searchHits.value = res.hits
    } catch {
      toast.error(t('conversation.chatroom.searchFailed'))
    }
  }

  return {
    searchQuery,
    searchHits,
    renderedSnippets,
    runSearch,
  }
}

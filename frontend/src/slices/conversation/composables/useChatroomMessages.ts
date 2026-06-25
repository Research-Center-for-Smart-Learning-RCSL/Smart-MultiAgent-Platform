// Composable: message pagination, CRUD, and markdown rendering for a chatroom.
// Extracted from ChatroomView.vue to separate data-fetching concerns from the
// view's template/UI logic (C4 SoC fix).

import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, nextTick, reactive, ref, watch } from 'vue'

import { useConfirmDialog, useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '@shared/stores/session'
import {
  deleteMessage as apiDeleteMessage,
  getMessage,
  listMessages,
  sendMessage,
  compactChatroom,
  getAttachment,
} from '../api'
import { renderMarkdown } from '../utils/renderMarkdown'
import { convKeys } from '../queries'
import type { Attachment, Message } from '../types'

// R13.21/R13.23: an author may edit their OWN message within 5 minutes; beyond
// that only Admin/Project Owner may. Agents never edit their own (R13.22). The
// backend is authoritative (If-Match + server-side window) — these gate the UI
// affordances only.
const EDIT_WINDOW_MS = 5 * 60 * 1000

export function useChatroomMessages(
  chatroomId: string,
  listRef: Readonly<{ value: HTMLElement | null }>,
) {
  const { t } = useI18n()
  const toast = useToast()
  const qc = useQueryClient()
  const session = useSessionStore()
  const { confirm } = useConfirmDialog()

  const myId = computed(() => session.me?.id ?? null)
  const isAdmin = computed(() => session.me?.is_admin ?? false)

  // ---------- permission helpers ------------------------------------------

  function isOwnUserMessage(m: Message): boolean {
    return m.sender_type === 'user' && !!myId.value && m.sender_id === myId.value
  }

  function canEdit(m: Message): boolean {
    if (isAdmin.value) return true
    return (
      isOwnUserMessage(m) &&
      Date.now() - new Date(m.created_at).getTime() < EDIT_WINDOW_MS
    )
  }

  function canDelete(m: Message): boolean {
    return isAdmin.value || isOwnUserMessage(m)
  }

  // ---------- pagination ---------------------------------------------------

  const PAGE_SIZE = 100
  const olderMessages = ref<Message[]>([])
  const hasOlderMessages = ref(true)
  const loadingOlder = ref(false)

  const query = useQuery({
    queryKey: convKeys.messages(chatroomId),
    queryFn: () => listMessages(chatroomId, { limit: PAGE_SIZE }),
  })

  watch(
    () => query.data.value,
    (recent) => {
      if (recent && recent.length < PAGE_SIZE && olderMessages.value.length === 0) {
        hasOlderMessages.value = false
      }
    },
    { immediate: true },
  )

  const messages = computed<Message[]>(() => {
    const recent = query.data.value ?? []
    const recentIds = new Set(recent.map((m) => m.id))
    const deduped = olderMessages.value.filter((m) => !recentIds.has(m.id))
    return [...deduped, ...recent].sort((a, b) =>
      a.created_at < b.created_at ? -1 : 1,
    )
  })

  async function loadEarlier(): Promise<void> {
    if (loadingOlder.value || !hasOlderMessages.value) return
    const oldest = messages.value[0]
    if (!oldest) return
    loadingOlder.value = true
    try {
      const page = await listMessages(chatroomId, {
        before: oldest.id,
        limit: PAGE_SIZE,
      })
      if (page.length < PAGE_SIZE) hasOlderMessages.value = false
      if (page.length === 0) return
      const existing = new Set([
        ...olderMessages.value.map((m) => m.id),
        ...(query.data.value ?? []).map((m) => m.id),
      ])
      const fresh = page.filter((m) => !existing.has(m.id))
      olderMessages.value = [...fresh, ...olderMessages.value]
    } catch {
      toast.error(t('conversation.chatroom.loadEarlierFailed'))
    } finally {
      loadingOlder.value = false
    }
  }

  // ---------- markdown rendering -------------------------------------------

  const rendered = reactive<Record<string, string>>({})
  const renderedSources = new Map<string, string>()

  watch(
    messages,
    (list) => {
      const seen = new Set<string>()
      for (const m of list) {
        seen.add(m.id)
        if (renderedSources.get(m.id) === m.content_md) continue
        rendered[m.id] = renderMarkdown(m.content_md)
        renderedSources.set(m.id, m.content_md)
      }
      for (const id of Object.keys(rendered)) {
        if (!seen.has(id)) {
          delete rendered[id]
          renderedSources.delete(id)
        }
      }
    },
    { immediate: true },
  )

  // ---------- compose & send -----------------------------------------------

  const draft = ref('')

  /** Send the current draft with the given attachment ids. Returns true on
   *  success so the caller can clear its own attachment state (the upload
   *  surface lives in useChatroomAttachments). */
  async function onSend(attachmentIds: string[] = []): Promise<boolean> {
    const text = draft.value.trim()
    if (!text && attachmentIds.length === 0) return false

    // /compact slash command (G.10): forces compaction on the active agent.
    if (text === '/compact') {
      draft.value = ''
      await compactChatroom(chatroomId)
      return true
    }

    try {
      await sendMessage(chatroomId, { content_md: text, attachment_ids: attachmentIds })
      draft.value = ''
      await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
      await nextTick()
      // jsdom (tests) has no Element.scrollTo.
      if (typeof listRef.value?.scrollTo === 'function') {
        listRef.value.scrollTo({ top: listRef.value.scrollHeight })
      }
      return true
    } catch {
      toast.error(t('conversation.chatroom.sendFailed'))
      return false
    }
  }

  // ---------- delete -------------------------------------------------------

  async function confirmDelete(m: Message): Promise<void> {
    const ok = await confirm({
      title: t('conversation.chatroom.deleteTitle'),
      message: t('conversation.chatroom.deleteConfirm'),
      variant: 'warning',
    })
    if (!ok) return
    try {
      await apiDeleteMessage(m.id)
      await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
    } catch {
      toast.error(t('conversation.chatroom.deleteFailed'))
    }
  }

  // ---------- attachment download ------------------------------------------

  function downloadAttachment(att: Attachment): void {
    const win = window.open('about:blank', '_blank')
    getAttachment(att.id)
      .then((dl) => {
        if (win) win.location.href = dl.url
        else window.location.href = dl.url
      })
      .catch(() => {
        win?.close()
        toast.error(t('conversation.chatroom.attachmentFailed'))
      })
  }

  // ---------- remote mutation sync (BUG-7) ----------------------------------

  function dropOlderMessage(messageId: string): void {
    olderMessages.value = olderMessages.value.filter((m) => m.id !== messageId)
  }

  async function refreshOlderMessage(messageId: string): Promise<void> {
    const idx = olderMessages.value.findIndex((m) => m.id === messageId)
    if (idx === -1) return
    try {
      const fresh = await getMessage(messageId)
      olderMessages.value = olderMessages.value.map((m) => (m.id === messageId ? fresh : m))
    } catch {
      // Message may have been deleted between the event and the fetch —
      // drop it so the stale version doesn't linger.
      dropOlderMessage(messageId)
    }
  }

  return {
    // pagination
    messages,
    hasOlderMessages,
    loadingOlder,
    loadEarlier,
    // rendered markdown
    rendered,
    // compose
    draft,
    onSend,
    // delete
    confirmDelete,
    // attachment download
    downloadAttachment,
    // permissions
    canEdit,
    canDelete,
    // remote mutation sync
    dropOlderMessage,
    refreshOlderMessage,
  }
}

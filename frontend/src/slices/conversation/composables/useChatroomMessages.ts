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
import type { Attachment, DisplayMessage, Message } from '../types'

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
    // An optimistic (not-yet-persisted) message has no server id to PATCH.
    if ((m as DisplayMessage)._status) return false
    if (isAdmin.value) return true
    return (
      isOwnUserMessage(m) &&
      Date.now() - new Date(m.created_at).getTime() < EDIT_WINDOW_MS
    )
  }

  function canDelete(m: Message): boolean {
    if ((m as DisplayMessage)._status) return false
    return isAdmin.value || isOwnUserMessage(m)
  }

  // ---------- pagination ---------------------------------------------------

  const PAGE_SIZE = 100
  const olderMessages = ref<Message[]>([])
  const hasOlderMessages = ref(true)
  const loadingOlder = ref(false)
  // Optimistic, not-yet-acknowledged sends (§7.2). They render immediately with
  // a "sending" state and are reconciled against the persisted message (or
  // rolled back) when the POST settles.
  const pendingMessages = ref<DisplayMessage[]>([])

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

  const messages = computed<DisplayMessage[]>(() => {
    const recent = query.data.value ?? []
    const recentIds = new Set(recent.map((m) => m.id))
    const deduped = olderMessages.value.filter((m) => !recentIds.has(m.id))
    const persisted = [...deduped, ...recent].sort((a, b) =>
      a.created_at < b.created_at ? -1 : 1,
    )
    // Pending sends always tail the list (they are the newest), regardless of
    // any clock skew between the optimistic timestamp and persisted messages.
    return pendingMessages.value.length
      ? [...persisted, ...pendingMessages.value]
      : persisted
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

    // Optimistic insert: show the message immediately in a "sending" state and
    // clear the composer, so the UI never waits on the round-trip (§7.2).
    const tempId = `pending-${crypto.randomUUID()}`
    const optimistic: DisplayMessage = {
      id: tempId,
      chatroom_id: chatroomId,
      sender_type: 'user',
      sender_id: myId.value,
      content_md: text,
      metadata: {},
      version: 0,
      created_at: new Date().toISOString(),
      edited_at: null,
      deleted_at: null,
      _status: 'sending',
    }
    pendingMessages.value = [...pendingMessages.value, optimistic]
    draft.value = ''
    await nextTick()
    // jsdom (tests) has no Element.scrollTo.
    if (typeof listRef.value?.scrollTo === 'function') {
      listRef.value.scrollTo({ top: listRef.value.scrollHeight })
    }

    try {
      const created = await sendMessage(chatroomId, {
        content_md: text,
        attachment_ids: attachmentIds,
      })
      // Seed the cache with the persisted message before dropping the optimistic
      // one, so the bubble never flickers out and back in. The WS echo /
      // refetch dedupes on id.
      qc.setQueryData<Message[]>(convKeys.messages(chatroomId), (prev) => {
        if (!prev) return [created]
        if (prev.some((x) => x.id === created.id)) return prev
        return [...prev, created]
      })
      pendingMessages.value = pendingMessages.value.filter((m) => m.id !== tempId)
      return true
    } catch {
      // Rollback: pull the optimistic message and surface the failure (§7.2).
      pendingMessages.value = pendingMessages.value.filter((m) => m.id !== tempId)
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
    const key = convKeys.messages(chatroomId)
    const prevRecent = qc.getQueryData<Message[]>(key)
    const prevOlder = olderMessages.value
    // Optimistic removal: drop it from both panes immediately. The list's
    // <TransitionGroup> animates the leave (§7.2 fade-out).
    qc.setQueryData<Message[]>(key, (prev) => prev?.filter((x) => x.id !== m.id))
    olderMessages.value = olderMessages.value.filter((x) => x.id !== m.id)
    try {
      await apiDeleteMessage(m.id)
    } catch {
      // Rollback both panes to exactly what they were before.
      if (prevRecent) qc.setQueryData(key, prevRecent)
      olderMessages.value = prevOlder
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

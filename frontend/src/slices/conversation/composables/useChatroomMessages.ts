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
  editMessage as apiEditMessage,
  listMessages,
  sendMessage,
  compactChatroom,
  getAttachment,
} from '../api'
import { tusUpload, resourceToAttachmentId } from '@shared/transport'
import { renderMarkdown } from '../utils/renderMarkdown'
import { convKeys } from '../queries'
import type { Attachment, Message } from '../types'

// R13.21/R13.23: an author may edit their OWN message within 5 minutes; beyond
// that only Admin/Project Owner may. Agents never edit their own (R13.22). The
// backend is authoritative (If-Match + server-side window) — these gate the UI
// affordances only.
const EDIT_WINDOW_MS = 5 * 60 * 1000

export interface PendingUpload {
  id: string
  filename: string
  progress: number
  attachmentId: string | null
}

export function useChatroomMessages(
  chatroomId: string,
  projectId: string,
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
    return [...olderMessages.value, ...recent].sort((a, b) =>
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
  const pendingUploads = ref<PendingUpload[]>([])

  async function onDrop(ev: DragEvent): Promise<void> {
    const files = Array.from(ev.dataTransfer?.files ?? [])
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

  async function onSend(): Promise<void> {
    const text = draft.value.trim()
    if (!text && pendingUploads.value.length === 0) return

    // /compact slash command (G.10): forces compaction on active agent.
    if (text === '/compact') {
      draft.value = ''
      await compactChatroom(chatroomId)
      return
    }

    const attachmentIds = pendingUploads.value
      .map((p) => p.attachmentId)
      .filter((x): x is string => !!x)
    try {
      await sendMessage(chatroomId, {
        content_md: text,
        attachment_ids: attachmentIds,
      })
      draft.value = ''
      pendingUploads.value = []
      await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
      await nextTick()
      listRef.value?.scrollTo({ top: listRef.value.scrollHeight })
    } catch {
      toast.error(t('conversation.chatroom.sendFailed'))
    }
  }

  // ---------- inline edit (R13.21) -----------------------------------------

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
    const idx = olderMessages.value.findIndex((m) => m.id === messageId)
    if (idx !== -1) {
      olderMessages.value = olderMessages.value.filter((m) => m.id !== messageId)
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
    pendingUploads,
    onDrop,
    onSend,
    // edit
    editingId,
    editDraft,
    startEdit,
    cancelEdit,
    saveEdit,
    // delete
    confirmDelete,
    // attachment
    downloadAttachment,
    // permissions
    canEdit,
    canDelete,
    // remote mutation sync
    dropOlderMessage,
  }
}

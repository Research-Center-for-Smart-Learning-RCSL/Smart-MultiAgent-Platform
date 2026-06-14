<template>
  <section
    class="chatroom"
    :class="{ 'chatroom--mobile': isMobile }"
  >
    <header>
      <h1>#{{ chatroomId.slice(0, 8) }}</h1>
      <span :class="['pill', connected ? 'on' : 'off']">
        {{ connected ? $t('conversation.chatroom.live') : $t('conversation.chatroom.offline') }}
      </span>
      <input
        v-model="searchQuery"
        :placeholder="$t('conversation.chatroom.searchPlaceholder')"
        @keyup.enter="runSearch"
      >
      <button @click="runExport">
        {{ $t('conversation.chatroom.export') }}
      </button>
      <span
        v-if="exportJob"
        class="export-status"
      >
        <a
          v-if="exportJob.status === 'ready' && exportJob.url"
          :href="exportJob.url"
          download
        >{{ $t('conversation.chatroom.exportDownload') }}</a>
        <span v-else>{{ $t(`conversation.chatroom.exportState.${exportJob.status}`) }}</span>
      </span>
    </header>

    <!-- Live approval cards (G.10) — rendered above messages when present. -->
    <div
      v-if="liveApprovals.length"
      class="approvals"
    >
      <ApprovalCard
        v-for="a in liveApprovals"
        :key="a.id"
        :approval="a"
        :agent-names="{}"
      />
    </div>

    <ol
      ref="listRef"
      class="messages"
    >
      <li
        v-for="m in messages"
        :key="m.id"
      >
        <div class="meta">
          <span>{{ m.sender_type }}</span>
          <time>{{ m.created_at }}</time>
          <span
            v-if="m.edited_at"
            class="edited"
          >{{ $t('conversation.chatroom.edited') }}</span>
          <span class="msg-actions">
            <button
              v-if="editingId !== m.id && canEdit(m)"
              type="button"
              class="link-btn"
              @click="startEdit(m)"
            >
              {{ $t('conversation.chatroom.edit') }}
            </button>
            <button
              v-if="canDelete(m)"
              type="button"
              class="link-btn link-btn--danger"
              @click="confirmDelete(m)"
            >
              {{ $t('conversation.chatroom.delete') }}
            </button>
          </span>
        </div>
        <!-- Inline editor (R13.21); otherwise the single v-html site (R24.41). -->
        <div
          v-if="editingId === m.id"
          class="md-edit"
        >
          <textarea
            v-model="editDraft"
            :aria-label="$t('conversation.chatroom.edit')"
          />
          <div class="md-edit__actions">
            <button
              type="button"
              @click="saveEdit"
            >
              {{ $t('conversation.chatroom.save') }}
            </button>
            <button
              type="button"
              @click="cancelEdit"
            >
              {{ $t('conversation.chatroom.cancel') }}
            </button>
          </div>
        </div>
        <div
          v-else
          class="md"
          v-html="rendered[m.id]"
        />
        <ul
          v-if="m.attachments && m.attachments.length"
          class="attachments"
        >
          <li
            v-for="att in m.attachments"
            :key="att.id"
          >
            <button
              v-if="att.status === 'active'"
              type="button"
              class="link-btn"
              @click="downloadAttachment(att)"
            >
              {{ att.filename }}
            </button>
            <span
              v-else
              class="attachment-gone"
            >{{ $t('conversation.chatroom.attachmentExpired', { name: att.filename }) }}</span>
          </li>
        </ul>
      </li>
      <!-- Transient streaming draft: agent.token deltas accumulate here until
           the persisted reply arrives via message.created (also rendered
           through renderMarkdown → DOMPurify, same XSS contract). -->
      <li
        v-if="streamingHtml"
        class="streaming"
        data-testid="streaming-draft"
      >
        <div class="meta">
          <span class="streaming-label">{{ $t('conversation.chatroom.agentStreaming') }}</span>
        </div>
        <div
          class="md"
          v-html="streamingHtml"
        />
      </li>
    </ol>

    <aside
      v-if="!isMobile"
      class="presence"
    >
      <h4>{{ $t('conversation.chatroom.online') }}</h4>
      <ul>
        <li
          v-for="uid in presenceList"
          :key="uid"
        >
          {{ uid.slice(0, 8) }}
        </li>
      </ul>
      <p v-if="store.agentThinking[chatroomId]">
        {{ $t('conversation.chatroom.agentThinking') }}
      </p>
    </aside>

    <form
      class="composer"
      @submit.prevent="onSend"
    >
      <textarea
        v-model="draft"
        :placeholder="$t('conversation.chatroom.composerPlaceholder')"
        :aria-label="$t('conversation.chatroom.composerPlaceholder')"
        @dragover.prevent
        @drop.prevent="onDrop"
      />
      <ul
        v-if="pendingUploads.length"
        class="attachments"
      >
        <li
          v-for="a in pendingUploads"
          :key="a.id"
        >
          {{ a.filename }} ({{ Math.round(a.progress * 100) }}%)
        </li>
      </ul>
      <button type="submit">
        {{ $t('conversation.chatroom.send') }}
      </button>
    </form>

    <section
      v-if="searchHits.length"
      class="search-results"
    >
      <h4>{{ $t('conversation.chatroom.searchResults') }}</h4>
      <ul>
        <li
          v-for="h in searchHits"
          :key="h.message_id"
          v-html="renderedSnippets[h.message_id]"
        />
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  onUpdated,
  reactive,
  ref,
  useTemplateRef,
  watch,
} from 'vue'
import { useRoute } from 'vue-router'

import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useBreakpoint, usePolling } from '@shared/composables'
import { useSessionStore } from '@slices/identity'
import {
  createExport,
  deleteMessage,
  editMessage,
  getAttachment,
  getExport,
  listMessages,
  searchMessages,
  sendMessage,
  compactChatroom,
} from '../api'
import { tusUpload, resourceToAttachmentId } from '@shared/transport'
import { useChatroomSocket } from '../composables/useChatroomSocket'
import { enhanceRenderedMarkdown, renderMarkdown, sanitizeSnippet } from '../lib/renderMarkdown'
import { convKeys } from '../queries'
import { useConversationStore } from '../stores/conversation'
import type { Attachment, ExportStatus, Message, SearchHit } from '../types'
import { ApprovalCard, useOrchestrationStore } from '@slices/workflow'

const { t } = useI18n()
const route = useRoute()
const qc = useQueryClient()
const store = useConversationStore()
const session = useSessionStore()
const chatroomId = route.params.chatroomId as string
const projectId = (route.params.projectId as string) || ''

// R13.21/R13.23: an author may edit their OWN message within 5 minutes; beyond
// that only Admin/Project Owner may. Agents never edit their own (R13.22). The
// backend is authoritative (If-Match + server-side window) — these gate the UI
// affordances only.
const EDIT_WINDOW_MS = 5 * 60 * 1000
const myId = computed(() => session.me?.id ?? null)
const isAdmin = computed(() => session.me?.is_admin ?? false)

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
  // R13.16: users delete within their scope; admins delete anything. We only
  // surface the control for own messages or admins; the backend enforces the
  // full scope (and a Project Owner deleting others' messages is allowed there).
  return isAdmin.value || isOwnUserMessage(m)
}

const { isMobile } = useBreakpoint()
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')
const draft = ref('')
const searchQuery = ref('')
const searchHits = ref<SearchHit[]>([])
const renderedSnippets = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const h of searchHits.value) out[h.message_id] = sanitizeSnippet(h.snippet)
  return out
})

const query = useQuery({
  queryKey: convKeys.messages(chatroomId),
  queryFn: () => listMessages(chatroomId, { limit: 100 }),
})
const messages = computed<Message[]>(() =>
  [...(query.data.value ?? [])].sort((a, b) =>
    a.created_at < b.created_at ? -1 : 1,
  ),
)

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

const { connected } = useChatroomSocket(chatroomId)
const orchStore = useOrchestrationStore()

// Streaming draft bubble (agent.token accumulation) — sanitised exactly like
// persisted messages.
const streamingHtml = computed(() => {
  const text = store.agentStream[chatroomId]
  return text ? renderMarkdown(text) : ''
})

// Agent failure surfaced by the socket layer: backend agent.finished{error}
// or the client-side thinking watchdog ('timeout'). Toast once, then clear.
watch(
  () => store.agentError[chatroomId],
  (err) => {
    if (!err) return
    ElMessage.error(
      t(
        err === 'timeout'
          ? 'conversation.chatroom.agentTimeout'
          : 'conversation.chatroom.agentFailed',
      ),
    )
    store.setAgentError(chatroomId, null)
  },
)

const presenceList = computed(() => {
  const set = store.presence[chatroomId]
  return set ? Array.from(set) : []
})

const liveApprovals = computed(() => orchStore.getApprovalsForRoom(chatroomId))

interface PendingUpload {
  id: string
  filename: string
  progress: number
  attachmentId: string | null
}
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
    ElMessage.error(t('conversation.chatroom.sendFailed'))
  }
}

async function runSearch(): Promise<void> {
  if (!searchQuery.value.trim()) {
    searchHits.value = []
    return
  }
  try {
    const res = await searchMessages(chatroomId, searchQuery.value.trim())
    searchHits.value = res.hits
  } catch {
    ElMessage.error(t('conversation.chatroom.searchFailed'))
  }
}

// ---- message edit / delete (R13.16 / R13.21) -----------------------------

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
    await editMessage(id, editVersion.value, text)
    cancelEdit()
    await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
  } catch {
    // 409 (stale If-Match) or 403 (past the 5-min window / not authorised).
    ElMessage.error(t('conversation.chatroom.editFailed'))
  }
}

async function downloadAttachment(att: Attachment): Promise<void> {
  // Fetch a short-lived presigned URL on demand (R13.10) and open it; the URL
  // points straight at object storage, so no bytes flow through the SPA.
  try {
    const dl = await getAttachment(att.id)
    window.open(dl.url, '_blank', 'noopener')
  } catch {
    ElMessage.error(t('conversation.chatroom.attachmentFailed'))
  }
}

async function confirmDelete(m: Message): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('conversation.chatroom.deleteConfirm'),
      t('conversation.chatroom.deleteTitle'),
      { type: 'warning' },
    )
  } catch {
    return // dismissed
  }
  try {
    await deleteMessage(m.id)
    await qc.invalidateQueries({ queryKey: convKeys.messages(chatroomId) })
  } catch {
    ElMessage.error(t('conversation.chatroom.deleteFailed'))
  }
}

// ---- export with status polling + download (R13.17) ----------------------
// The export runs in a worker; there is no WS channel for it, so poll the job
// until it settles via the shared usePolling primitive (transient blips
// reschedule rather than strand the UI; timers cleared on unmount).

const EXPORT_TERMINAL = new Set<ExportStatus['status']>(['ready', 'failed'])
const exportJob = ref<Pick<ExportStatus, 'status' | 'url'> | null>(null)

const exportPoll = usePolling<ExportStatus>((jobId) => getExport(jobId), {
  maxAttempts: 60, // ~3 min at 3 s/poll
  isTerminal: (s) => EXPORT_TERMINAL.has(s.status),
  onResult: (_jobId, s) => {
    exportJob.value = { status: s.status, url: s.url }
  },
})

async function runExport(): Promise<void> {
  try {
    const { job_id, status } = await createExport(chatroomId)
    exportJob.value = { status: status as ExportStatus['status'], url: null }
    exportPoll.start(job_id)
  } catch {
    ElMessage.error(t('conversation.chatroom.exportFailed'))
  }
}

// KaTeX/Mermaid post-processing (FE-12). `onUpdated` fires on every reactive
// change — each presence blip, typing indicator, new message — and the
// Mermaid pass is async, so naive invocation lets overlapping runs race over
// the same DOM nodes. We debounce so a burst collapses to one pass, and an
// in-flight guard serialises runs; a change arriving mid-pass queues exactly
// one follow-up so the latest DOM is always reflected.
const ENHANCE_DEBOUNCE_MS = 120
let enhanceTimer: ReturnType<typeof setTimeout> | null = null
let enhanceInFlight = false
let enhanceQueued = false

async function runEnhance(): Promise<void> {
  if (enhanceInFlight) {
    enhanceQueued = true
    return
  }
  if (!listRef.value) return
  enhanceInFlight = true
  try {
    await enhanceRenderedMarkdown(listRef.value)
  } catch {
    // Best-effort; rendering errors must not crash the chatroom.
  } finally {
    enhanceInFlight = false
  }
  if (enhanceQueued) {
    enhanceQueued = false
    scheduleEnhance()
  }
}

function scheduleEnhance(): void {
  if (enhanceTimer !== null) clearTimeout(enhanceTimer)
  enhanceTimer = setTimeout(() => {
    enhanceTimer = null
    void runEnhance()
  }, ENHANCE_DEBOUNCE_MS)
}

onMounted(scheduleEnhance)
onUpdated(scheduleEnhance)
onBeforeUnmount(() => {
  if (enhanceTimer !== null) clearTimeout(enhanceTimer)
})
</script>

<style scoped>
.chatroom {
  display: grid;
  grid-template-columns: 1fr 240px;
  grid-template-rows: auto 1fr auto auto;
  gap: 0.5rem;
  height: 100%;
}
.messages {
  grid-column: 1;
  grid-row: 2;
  overflow-y: auto;
  list-style: none;
  padding: 0.5rem;
}
.presence {
  grid-column: 2;
  grid-row: 2 / 4;
}
.composer {
  grid-column: 1;
  grid-row: 3;
}
.search-results {
  grid-column: 1 / 3;
  grid-row: 4;
}
.pill.on {
  color: #0a0;
}
.pill.off {
  color: #a00;
}
.chatroom--mobile {
  grid-template-columns: 1fr;
}
.chatroom--mobile .presence {
  display: none;
}
.meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.edited {
  color: var(--color-muted, #6b7280);
  font-size: 0.75rem;
}
.msg-actions {
  margin-left: auto;
  display: inline-flex;
  gap: 0.5rem;
}
.link-btn {
  background: none;
  border: none;
  padding: 0;
  font-size: 0.8rem;
  color: var(--color-primary, #2563eb);
  cursor: pointer;
}
.link-btn--danger {
  color: var(--color-danger, #b91c1c);
}
.md-edit textarea {
  width: 100%;
  min-height: 4rem;
}
.md-edit__actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.25rem;
}
.export-status {
  margin-left: 0.5rem;
  font-size: 0.85rem;
}
.attachment-gone {
  color: var(--color-muted, #6b7280);
  font-style: italic;
  font-size: 0.85rem;
}
</style>

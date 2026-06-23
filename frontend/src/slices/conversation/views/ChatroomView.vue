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
        v-if="hasOlderMessages"
        class="load-earlier"
      >
        <button
          type="button"
          :disabled="loadingOlder"
          @click="loadEarlier"
        >
          {{ loadingOlder ? $t('conversation.chatroom.loadingEarlier') : $t('conversation.chatroom.loadEarlier') }}
        </button>
      </li>
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
              v-else-if="att.status === 'quarantined'"
              class="attachment-gone"
            >{{ $t('conversation.chatroom.attachmentQuarantined', { name: att.filename }) }}</span>
            <span
              v-else
              class="attachment-gone"
            >{{ $t('conversation.chatroom.attachmentExpired', { name: att.filename }) }}</span>
          </li>
        </ul>
      </li>
      <!-- Transient streaming drafts: per-agent agent.token deltas accumulate
           here until the persisted reply arrives via message.created (also
           rendered through renderMarkdown → DOMPurify, same XSS contract). -->
      <li
        v-for="[agentId, html] in streamingEntries"
        :key="`stream-${agentId}`"
        class="streaming"
        data-testid="streaming-draft"
      >
        <div class="meta">
          <span class="streaming-label">{{ $t('conversation.chatroom.agentStreaming') }}</span>
        </div>
        <div
          class="md"
          v-html="html"
        />
      </li>
    </ol>

    <ChatroomPresence
      v-if="!isMobile"
      :presence-list="presenceList"
      :agent-thinking="store.isAnyAgentThinking(chatroomId) ? 'yes' : null"
    />

    <p
      v-if="typingList.length"
      class="typing-indicator"
    >
      {{ typingList.map((uid) => uid.slice(0, 8)).join(', ') }}
      {{ $t('conversation.chatroom.typing') }}
    </p>

    <ChatroomComposer
      v-model="draft"
      :pending-uploads="pendingUploads.length"
      @submit="onSend"
      @typing="emitTyping"
      @drop="onDrop"
    >
      <template #pending-uploads>
        <li
          v-for="a in pendingUploads"
          :key="a.id"
        >
          {{ a.filename }} ({{ Math.round(a.progress * 100) }}%)
        </li>
      </template>
    </ChatroomComposer>

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
import {
  computed,
  onBeforeUnmount,
  onMounted,
  onUpdated,
  useTemplateRef,
  watch,
} from 'vue'
import { useRoute } from 'vue-router'

import { useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { useBreakpoint } from '@shared/composables'
import { useSessionStore } from '@shared/stores/session'
import { useChatroomSocket } from '../composables/useChatroomSocket'
import { useChatroomMessages } from '../composables/useChatroomMessages'
import { useChatroomSearch } from '../composables/useChatroomSearch'
import { useChatroomExport } from '../composables/useChatroomExport'
import { enhanceRenderedMarkdown, renderMarkdown } from '../utils/renderMarkdown'
import { useConversationStore } from '../stores/conversation'
import { ApprovalCard } from '@slices/workflow'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import ChatroomComposer from '../components/ChatroomComposer.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const store = useConversationStore()
const session = useSessionStore()
const chatroomId = route.params.chatroomId as string
const projectId = (route.params.projectId as string) || ''

const myId = computed(() => session.me?.id ?? null)

const { isMobile } = useBreakpoint()
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')

// ---- composables ----------------------------------------------------------

const {
  messages,
  hasOlderMessages,
  loadingOlder,
  loadEarlier,
  rendered,
  draft,
  pendingUploads,
  onDrop,
  onSend,
  editingId,
  editDraft,
  startEdit,
  cancelEdit,
  saveEdit,
  confirmDelete,
  downloadAttachment,
  canEdit,
  canDelete,
  dropOlderMessage,
  refreshOlderMessage,
} = useChatroomMessages(chatroomId, projectId, listRef)

const {
  searchQuery,
  searchHits,
  renderedSnippets,
  runSearch,
} = useChatroomSearch(chatroomId)

const {
  exportJob,
  runExport,
} = useChatroomExport(chatroomId)

// ---- WebSocket + real-time state ------------------------------------------

let typingTimer: ReturnType<typeof setTimeout> | null = null
const TYPING_DEBOUNCE_MS = 3000

const { connected, channel: wsChannel } = useChatroomSocket(chatroomId)
const orchStore = useOrchestrationStore()

wsChannel.subscribe('message.updated', (ev) => void refreshOlderMessage(ev.message_id as string))
wsChannel.subscribe('message.deleted', (ev) => dropOlderMessage(ev.message_id as string))

function emitTyping(): void {
  if (typingTimer === null) {
    wsChannel.send({ type: 'typing.start' })
  } else {
    clearTimeout(typingTimer)
  }
  typingTimer = setTimeout(() => {
    wsChannel.send({ type: 'typing.stop' })
    typingTimer = null
  }, TYPING_DEBOUNCE_MS)
}

onBeforeUnmount(() => {
  if (typingTimer !== null) {
    clearTimeout(typingTimer)
    typingTimer = null
  }
})

const typingList = computed(() => {
  const set = store.typingUsers[chatroomId]
  if (!set) return []
  return Array.from(set).filter((uid) => uid !== myId.value)
})

// Per-agent streaming draft bubbles — memoised so only the agent whose text
// changed gets re-rendered (renderMarkdown + DOMPurify is expensive at token
// frequency). The cache maps agentId → {source, html}.
const _streamCache = new Map<string, { source: string; html: string }>()
const streamingEntries = computed<[string, string][]>(() => {
  const roomStreams = store.agentStreams[chatroomId]
  if (!roomStreams) {
    _streamCache.clear()
    return []
  }
  const activeIds = new Set<string>()
  const entries: [string, string][] = []
  for (const [agentId, text] of Object.entries(roomStreams)) {
    if (!text) continue
    activeIds.add(agentId)
    const cached = _streamCache.get(agentId)
    if (cached && cached.source === text) {
      entries.push([agentId, cached.html])
    } else {
      const html = renderMarkdown(text)
      _streamCache.set(agentId, { source: text, html })
      entries.push([agentId, html])
    }
  }
  for (const key of _streamCache.keys()) {
    if (!activeIds.has(key)) _streamCache.delete(key)
  }
  return entries
})

// Agent failure surfaced by the socket layer: backend agent.finished{error}
// or the client-side thinking watchdog ('timeout'). Toast once, then clear.
watch(
  () => store.agentError[chatroomId],
  (err) => {
    if (!err) return
    toast.error(
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

// ---- KaTeX/Mermaid post-processing (FE-12) --------------------------------
// `onUpdated` fires on every reactive change — each presence blip, typing
// indicator, new message — and the Mermaid pass is async, so naive invocation
// lets overlapping runs race over the same DOM nodes. We debounce so a burst
// collapses to one pass, and an in-flight guard serialises runs; a change
// arriving mid-pass queues exactly one follow-up so the latest DOM is always
// reflected.
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
.chatroom h1 {
  font-size: 1.25rem;
  font-weight: 600;
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
  color: var(--color-success);
}
.pill.off {
  color: var(--color-danger);
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
  color: var(--color-muted);
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
  font-size: 0.875rem;
  color: var(--color-accent);
  cursor: pointer;
}
.link-btn--danger {
  color: var(--color-danger);
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
  font-size: 0.875rem;
}
.attachment-gone {
  color: var(--color-muted);
  font-style: italic;
  font-size: 0.875rem;
}
.load-earlier {
  text-align: center;
  padding: 0.5rem 0;
}
.load-earlier button {
  font-size: 0.875rem;
  color: var(--color-accent);
  background: none;
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  padding: 0.25rem 0.75rem;
  cursor: pointer;
}
.load-earlier button:disabled {
  opacity: 0.5;
  cursor: default;
}
.typing-indicator {
  grid-column: 1;
  padding: 0 0.5rem;
  font-size: 0.875rem;
  color: var(--color-muted);
  font-style: italic;
  min-height: 1.2em;
}
</style>

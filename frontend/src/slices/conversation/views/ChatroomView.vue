<template>
  <section
    class="chatroom"
    :class="{ 'chatroom--mobile': isMobile }"
  >
    <ChatroomHeader
      class="chatroom__header"
      :room-name="roomName"
      :connected="connected"
      :is-mobile="isMobile"
      @back="goBack"
      @search="searchOpen = true"
      @settings="goSettings"
      @export="openExport"
      @toggle-agents="agentsDrawerOpen = true"
      @toggle-people="peopleDrawerOpen = true"
    />

    <ChatroomAgentSidebar
      v-if="!isMobile"
      class="chatroom__agents"
      :agents="agentList"
    />

    <div class="chatroom__feed">
      <ChatroomSearchPanel
        v-if="searchOpen"
        :query="searchQuery"
        :hits="searchHits"
        :rendered-snippets="renderedSnippets"
        :searching="searching"
        @update:query="searchQuery = $event"
        @search="doSearch"
        @close="searchOpen = false"
        @select="onSelectHit"
      />

      <ol
        ref="listRef"
        class="messages"
      >
        <li v-if="hasOlderMessages">
          <ChatroomLoadEarlier
            :loading="loadingOlder"
            @load="onLoadEarlier"
          />
        </li>

        <ChatroomMessageBubble
          v-for="m in messages"
          :key="m.id"
          :message="m"
          :html="rendered[m.id] ?? ''"
          :sender-name="senderName(m)"
          :editing="editingId === m.id"
          :edit-draft="editDraft"
          :can-edit="canEdit(m)"
          :can-delete="canDelete(m)"
          @start-edit="startEdit(m)"
          @save-edit="saveEdit"
          @cancel-edit="cancelEdit"
          @delete="confirmDelete(m)"
          @copy="copyMessage(m)"
          @download="downloadAttachment"
          @update:edit-draft="editDraft = $event"
        />

        <li
          v-for="a in liveApprovals"
          :key="`approval-${a.id}`"
        >
          <ApprovalCard
            :approval="a"
            :agent-names="{}"
          />
        </li>

        <ChatroomStreamingBubble
          v-for="[agentId, html] in streamingEntries"
          :key="`stream-${agentId}`"
          :html="html"
          :agent-name="agentId.slice(0, 8)"
        />

        <li v-if="!messages.length && !streamingEntries.length && !liveApprovals.length">
          <SEmptyState
            :icon="ChatBubbleLeftRightIcon"
            :title="t('conversation.chatroom.emptyTitle')"
            :text="t('conversation.chatroom.emptyText')"
          />
        </li>
      </ol>

      <div
        v-if="showPill"
        class="chatroom__pill"
      >
        <ChatroomNewMessagesPill
          :count="newCount"
          @click="scrollToBottom(true)"
        />
      </div>
    </div>

    <ChatroomTypingIndicator
      class="chatroom__typing"
      :names="typingNames"
    />

    <ChatroomComposer
      v-model="draft"
      class="chatroom__composer"
      :pending-uploads="pendingUploads"
      :disabled="!connected"
      @submit="onSend"
      @typing="emitTyping"
      @drop="onDrop"
      @pick-files="uploadFiles"
      @remove-upload="removeUpload"
    />

    <ChatroomPresence
      v-if="!isMobile"
      class="chatroom__presence"
      :online-users="onlineUsers"
      :agents="agentList"
    />

    <!-- Mobile drawers -->
    <SDrawer
      v-if="isMobile"
      :open="agentsDrawerOpen"
      side="left"
      :title="t('conversation.chatroom.agents')"
      @close="agentsDrawerOpen = false"
    >
      <ChatroomAgentSidebar :agents="agentList" />
    </SDrawer>
    <SDrawer
      v-if="isMobile"
      :open="peopleDrawerOpen"
      side="right"
      :title="t('conversation.chatroom.people')"
      @close="peopleDrawerOpen = false"
    >
      <ChatroomPresence
        :online-users="onlineUsers"
        :agents="agentList"
      />
    </SDrawer>

    <ChatroomExportModal
      :open="exportOpen"
      :job="exportJob"
      @close="exportOpen = false"
      @submit="onExportSubmit"
    />
  </section>
</template>

<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  onUpdated,
  ref,
  useTemplateRef,
  watch,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'

import { useToast, useBreakpoint } from '@shared/composables'
import { SDrawer, SEmptyState } from '@shared/ui'
import { ChatBubbleLeftRightIcon } from '@heroicons/vue/24/outline'
import { useSessionStore } from '@shared/stores/session'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import { ApprovalCard } from '@slices/workflow'

import { useChatroomSocket } from '../composables/useChatroomSocket'
import { useChatroomMessages } from '../composables/useChatroomMessages'
import { useChatroomSearch } from '../composables/useChatroomSearch'
import { useChatroomExport } from '../composables/useChatroomExport'
import { useChatroomScroll } from '../composables/useChatroomScroll'
import { useConversationStore } from '../stores/conversation'
import { enhanceRenderedMarkdown, renderMarkdown } from '../utils/renderMarkdown'
import { getChatroom, listChatroomAgents, type ExportOptions } from '../api'
import { convKeys } from '../queries'
import type { AgentStatus } from '../components/ChatroomAgentSidebar.vue'
import type { Message, SearchHit } from '../types'

import ChatroomHeader from '../components/ChatroomHeader.vue'
import ChatroomAgentSidebar from '../components/ChatroomAgentSidebar.vue'
import ChatroomMessageBubble from '../components/ChatroomMessageBubble.vue'
import ChatroomStreamingBubble from '../components/ChatroomStreamingBubble.vue'
import ChatroomPresence from '../components/ChatroomPresence.vue'
import ChatroomComposer from '../components/ChatroomComposer.vue'
import ChatroomTypingIndicator from '../components/ChatroomTypingIndicator.vue'
import ChatroomSearchPanel from '../components/ChatroomSearchPanel.vue'
import ChatroomExportModal from '../components/ChatroomExportModal.vue'
import ChatroomNewMessagesPill from '../components/ChatroomNewMessagesPill.vue'
import ChatroomLoadEarlier from '../components/ChatroomLoadEarlier.vue'

const { t } = useI18n()
const toast = useToast()
const route = useRoute()
const router = useRouter()
const store = useConversationStore()
const session = useSessionStore()
const orchStore = useOrchestrationStore()
const chatroomId = route.params.chatroomId as string
const projectId = (route.params.projectId as string) || ''

const myId = computed(() => session.me?.id ?? null)
const { isMobile } = useBreakpoint()
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')

// ---- room + bound agents --------------------------------------------------

const roomQuery = useQuery({
  queryKey: convKeys.chatroom(chatroomId),
  queryFn: () => getChatroom(chatroomId),
  retry: false,
})
const roomName = computed(() => roomQuery.data.value?.name ?? `#${chatroomId.slice(0, 8)}`)

const boundAgentsQuery = useQuery({
  queryKey: ['conversation', 'chatroom-agents', chatroomId],
  queryFn: () => listChatroomAgents(chatroomId),
  retry: false,
})

function agentStatus(id: string): AgentStatus {
  if (store.agentStreams[chatroomId]?.[id]) return 'streaming'
  if (store.agentThinking[chatroomId]?.has(id)) return 'thinking'
  return 'idle'
}

const agentList = computed(() =>
  (boundAgentsQuery.data.value ?? []).map((id) => ({
    id,
    name: id.slice(0, 8),
    status: agentStatus(id),
  })),
)

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
  uploadFiles,
  removeUpload,
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

const { searchQuery, searchHits, renderedSnippets, runSearch } = useChatroomSearch(chatroomId)
const { exportJob, runExport } = useChatroomExport(chatroomId)
const messageCount = computed(() => messages.value.length)
const { showPill, newCount, scrollToBottom, maybeStick, captureBeforePrepend, restoreAfterPrepend } =
  useChatroomScroll(listRef, messageCount)

// ---- WebSocket + real-time state ------------------------------------------

let typingTimer: ReturnType<typeof setTimeout> | null = null
const TYPING_DEBOUNCE_MS = 3000

const { connected, channel: wsChannel } = useChatroomSocket(chatroomId)

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

const typingNames = computed(() => {
  const set = store.typingUsers[chatroomId]
  if (!set) return []
  return Array.from(set)
    .filter((uid) => uid !== myId.value)
    .map((uid) => uid.slice(0, 8))
})

const onlineUsers = computed(() => {
  const set = store.presence[chatroomId]
  if (!set) return []
  return Array.from(set).map((id) => ({ id, isYou: id === myId.value }))
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

const liveApprovals = computed(() => orchStore.getApprovalsForRoom(chatroomId))

// ---- header actions -------------------------------------------------------

const searchOpen = ref(false)
const searching = ref(false)
const exportOpen = ref(false)
const agentsDrawerOpen = ref(false)
const peopleDrawerOpen = ref(false)

function goBack(): void {
  router.back()
}

function goSettings(): void {
  void router.push({ name: 'conversation.chatroom.settings', params: { chatroomId } })
}

function senderName(m: Message): string {
  return m.sender_id ? m.sender_id.slice(0, 8) : m.sender_type
}

function copyMessage(m: Message): void {
  navigator.clipboard?.writeText(m.content_md).catch(() => {})
}

async function doSearch(): Promise<void> {
  searching.value = true
  try {
    await runSearch()
  } finally {
    searching.value = false
  }
}

function onSelectHit(_hit: SearchHit): void {
  searchOpen.value = false
}

async function onLoadEarlier(): Promise<void> {
  captureBeforePrepend()
  await loadEarlier()
  restoreAfterPrepend()
}

function openExport(): void {
  exportJob.value = null
  exportOpen.value = true
}

function onExportSubmit(opts: ExportOptions): void {
  void runExport(opts)
}

// ---- KaTeX/Mermaid post-processing (FE-12) --------------------------------
// `onUpdated` fires on every reactive change; the Mermaid pass is async, so we
// debounce a burst into one pass and serialise overlapping runs. Streaming
// growth also re-pins the feed to the bottom if the user was already there.
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
onUpdated(() => {
  scheduleEnhance()
  maybeStick()
})
onBeforeUnmount(() => {
  if (enhanceTimer !== null) clearTimeout(enhanceTimer)
})
</script>

<style scoped>
.chatroom {
  display: grid;
  grid-template-columns: 220px 1fr 200px;
  grid-template-rows: 48px 1fr auto auto;
  height: 100%;
  overflow: hidden;
}

.chatroom__header {
  grid-column: 1 / -1;
  grid-row: 1;
}

.chatroom__agents {
  grid-column: 1;
  grid-row: 2 / -1;
}

.chatroom__feed {
  grid-column: 2;
  grid-row: 2;
  position: relative;
  min-height: 0;
  overflow: hidden;
}

.messages {
  height: 100%;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 16px;
}

.chatroom__typing {
  grid-column: 2;
  grid-row: 3;
}

.chatroom__composer {
  grid-column: 2;
  grid-row: 4;
}

.chatroom__presence {
  grid-column: 3;
  grid-row: 2 / -1;
}

.chatroom__pill {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
}

/* Mobile: single column; side panels become drawers. */
.chatroom--mobile {
  grid-template-columns: 1fr;
  grid-template-rows: 48px 1fr auto auto;
}

.chatroom--mobile .chatroom__feed,
.chatroom--mobile .chatroom__typing,
.chatroom--mobile .chatroom__composer {
  grid-column: 1;
}
</style>

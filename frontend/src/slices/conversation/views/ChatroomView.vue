<template>
  <section
    class="chatroom"
    :class="{ 'chatroom--mobile': isMobile, 'chatroom--tablet': isTablet }"
    :style="isMobile ? { '--kb-inset': `${keyboardInset}px` } : undefined"
  >
    <ChatroomHeader
      class="chatroom__header"
      :room-name="roomName"
      :connection-state="connectionState"
      :is-mobile="isMobile"
      :is-desktop="isDesktop"
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
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        :aria-label="t('conversation.chatroom.messageList')"
      >
        <li v-if="hasOlderMessages">
          <ChatroomLoadEarlier
            :loading="loadingOlder"
            @load="onLoadEarlier"
          />
        </li>

        <TransitionGroup name="msg">
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
            :flash="highlightId === m.id"
            @start-edit="startEdit(m)"
            @save-edit="saveEdit"
            @cancel-edit="cancelEdit"
            @delete="confirmDelete(m)"
            @copy="copyMessage(m)"
            @download="downloadAttachment"
            @update:edit-draft="editDraft = $event"
          />
        </TransitionGroup>

        <li
          v-for="a in liveApprovals"
          :key="`approval-${a.id}`"
        >
          <ApprovalCard
            :approval="a"
            :agent-names="agentNames"
          />
        </li>

        <ChatroomStreamingBubble
          v-for="[agentId, html] in streamingEntries"
          :key="`stream-${agentId}`"
          :html="html"
          :agent-name="agentNames[agentId] ?? agentId.slice(0, 8)"
          aria-live="off"
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
      @submit="send"
      @typing="emitTyping"
      @drop="onDrop"
      @pick-files="uploadFiles"
      @remove-upload="removeUpload"
    />

    <ChatroomPresence
      v-if="isDesktop"
      class="chatroom__presence"
      :online-users="onlineUsers"
      :agents="agentList"
    />

    <!-- Agents drawer: mobile only (tablet keeps the agents rail). -->
    <SDrawer
      v-if="isMobile"
      :open="agentsDrawerOpen"
      side="left"
      :title="t('conversation.chatroom.agents')"
      @close="agentsDrawerOpen = false"
    >
      <ChatroomAgentSidebar :agents="agentList" />
    </SDrawer>
    <!-- Presence drawer: mobile + tablet (presence rail only exists at lg+). -->
    <SDrawer
      v-if="!isDesktop"
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuery } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'

import { useToast, useBreakpoint, useVisualViewport } from '@shared/composables'
import { SDrawer, SEmptyState } from '@shared/ui'
import { ChatBubbleLeftRightIcon } from '@heroicons/vue/24/outline'
import { useSessionStore } from '@shared/stores/session'
import { useOrchestrationStore } from '@shared/stores/orchestration'
import { ApprovalCard } from '@slices/workflow'

import { useChatroomSocket } from '../composables/useChatroomSocket'
import { useChatroomMessages } from '../composables/useChatroomMessages'
import { useChatroomMessageEditing } from '../composables/useChatroomMessageEditing'
import { useChatroomAttachments } from '../composables/useChatroomAttachments'
import { useChatroomSearch } from '../composables/useChatroomSearch'
import { useChatroomExport } from '../composables/useChatroomExport'
import { useChatroomScroll } from '../composables/useChatroomScroll'
import { useAgentStreams } from '../composables/useAgentStreams'
import { useMarkdownEnhance } from '../composables/useMarkdownEnhance'
import { useConversationStore } from '../stores/conversation'
import { getChatroom, getWorkspace, listChatroomAgents, listProjectAgents, type ExportOptions } from '../api'
import { convKeys } from '../queries'
import type { AgentStatus } from '../components/ChatroomAgentStatusItem.vue'
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
const { isMobile, isTablet, isDesktop } = useBreakpoint()
const { keyboardInset } = useVisualViewport(() => isMobile.value)
store.setActive(chatroomId)

const listRef = useTemplateRef<HTMLElement>('listRef')

// ---- room + bound agents --------------------------------------------------
// Both queries degrade gracefully on purpose: a guest who can't read the room
// metadata (403) still gets a usable view — roomName falls back to the id and
// the agent list simply stays empty. Do not add error UI here.

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

// Resolve workspace → project → agents to get agent display names. Each
// query gates on the previous via `enabled`, so missing room data does not
// trigger errors; the names map simply stays empty and falls back to the
// truncated id.
const workspaceQuery = useQuery({
  queryKey: computed(() => ['conversation', 'workspace', roomQuery.data.value?.workspace_id]),
  queryFn: () => getWorkspace(roomQuery.data.value!.workspace_id),
  enabled: computed(() => !!roomQuery.data.value?.workspace_id),
  retry: false,
})

const projectAgentsQuery = useQuery({
  queryKey: computed(() => ['conversation', 'project-agents', workspaceQuery.data.value?.project_id]),
  queryFn: () => listProjectAgents(workspaceQuery.data.value!.project_id),
  enabled: computed(() => !!workspaceQuery.data.value?.project_id),
  retry: false,
})

const agentNames = computed<Record<string, string>>(() => {
  const agents = projectAgentsQuery.data.value
  if (!agents) return {}
  const map: Record<string, string> = {}
  for (const a of agents) {
    map[a.id] = a.name
  }
  return map
})

function agentStatus(id: string): AgentStatus {
  if (store.agentStreams[chatroomId]?.[id]) return 'streaming'
  if (store.agentThinking[chatroomId]?.has(id)) return 'thinking'
  return 'idle'
}

const agentList = computed(() =>
  (boundAgentsQuery.data.value ?? []).map((id) => ({
    id,
    name: agentNames.value[id] ?? id.slice(0, 8),
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
  onSend,
  confirmDelete,
  downloadAttachment,
  canEdit,
  canDelete,
  dropOlderMessage,
  refreshOlderMessage,
} = useChatroomMessages(chatroomId, listRef)

const { editingId, editDraft, startEdit, cancelEdit, saveEdit } =
  useChatroomMessageEditing(chatroomId)

const {
  pendingUploads,
  uploadFiles,
  onDrop,
  removeUpload,
  attachmentIds,
  clear: clearAttachments,
} = useChatroomAttachments(chatroomId, projectId)

const { streamingEntries } = useAgentStreams(chatroomId)

const { searchQuery, searchHits, renderedSnippets, runSearch } = useChatroomSearch(chatroomId)
const { exportJob, runExport } = useChatroomExport(chatroomId)
const messageCount = computed(() => messages.value.length)
const {
  showPill,
  newCount,
  highlightId,
  scrollToBottom,
  scrollToMessage,
  maybeStick,
  captureBeforePrepend,
  restoreAfterPrepend,
} = useChatroomScroll(listRef, messageCount)

// Debounced KaTeX/Mermaid post-processing; re-pin scroll after each update.
useMarkdownEnhance(listRef, { onAfterUpdate: maybeStick })

/** Send the draft + resolved attachments, clearing uploads on success. */
async function send(): Promise<void> {
  const ok = await onSend(attachmentIds())
  if (ok) clearAttachments()
}

// ---- WebSocket + real-time state ------------------------------------------

let typingTimer: ReturnType<typeof setTimeout> | null = null
const TYPING_DEBOUNCE_MS = 3000

const { connected, connectionState, channel: wsChannel } = useChatroomSocket(chatroomId)

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

function onKeyDown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    const tag = (document.activeElement as HTMLElement | null)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeyDown)
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
  if (m.sender_type === 'agent' && m.sender_id) {
    return agentNames.value[m.sender_id] ?? m.sender_id.slice(0, 8)
  }
  return m.sender_id ? m.sender_id.slice(0, 8) : m.sender_type
}

async function copyMessage(m: Message): Promise<void> {
  if (!navigator.clipboard) {
    toast.error(t('conversation.chatroom.copyFailed'))
    return
  }
  try {
    await navigator.clipboard.writeText(m.content_md)
    toast.success(t('conversation.chatroom.copied'))
  } catch {
    toast.error(t('conversation.chatroom.copyFailed'))
  }
}

async function doSearch(): Promise<void> {
  searching.value = true
  try {
    await runSearch()
  } finally {
    searching.value = false
  }
}

function onSelectHit(hit: SearchHit): void {
  searchOpen.value = false
  // The panel sits over the feed; let it unmount before scrolling so the
  // target message is not obscured. A hit may reference a message that has
  // not been paginated into the feed yet — there is no "load-around" endpoint,
  // so we tell the user rather than scrolling to nothing.
  void nextTick(() => {
    if (!scrollToMessage(hit.message_id)) {
      toast.info(t('conversation.chatroom.searchJumpUnavailable'))
    }
  })
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

/* Real-time list animations (§7.2 / §7.5): new messages slide in, deleted ones
   fade out. Initial-render items are NOT animated (no `appear`), so opening a
   busy room does not flush the whole backlog through the transition. */
.msg-enter-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}

.msg-leave-active {
  transition:
    opacity 150ms ease,
    transform 150ms ease;
}

.msg-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.msg-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* On send success the optimistic placeholder (a `pending-<uuid>` vnode key) is
   replaced by its persisted twin (the real id) at the same position — a key
   swap Vue animates as leave+enter. Drop the placeholder's leave transition so
   that swap reads as a single clean settle, not a cross-fade flash. */
.bubble-row--pending.msg-leave-active {
  transition: none;
}

@media (prefers-reduced-motion: reduce) {
  .msg-enter-active,
  .msg-leave-active {
    transition: none;
  }
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

/* Tablet (768-1023): 2-column — agents rail + feed. Presence is a drawer. */
.chatroom--tablet {
  grid-template-columns: 200px 1fr;
}

/* Mobile: single column; side panels become drawers. The grid shrinks by the
   keyboard overlap so the composer stays visible above the virtual keyboard. */
.chatroom--mobile {
  grid-template-columns: 1fr;
  grid-template-rows: 48px 1fr auto auto;
  height: calc(100% - var(--kb-inset, 0px));
}

.chatroom--mobile .chatroom__feed,
.chatroom--mobile .chatroom__typing,
.chatroom--mobile .chatroom__composer {
  grid-column: 1;
}
</style>
